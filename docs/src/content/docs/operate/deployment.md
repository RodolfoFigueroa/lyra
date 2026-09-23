---
title: Deployment
description: Deploy API and worker containers with explicit config, secrets, state, and routing.
---

`lyra.toml` is the authoritative configuration, read once at process startup.
API and workers read the same TOML and environment variables and share one
writable `/lyra_data` volume.

## Required deployment inputs

- Mount `lyra.toml` at `/lyra_data/config/lyra.toml` read-only.
- Mount the Earth Engine key at its configured absolute path read-only.
- Set PostgreSQL host, port, database name, and user in `[database]`.
- Supply `LYRA_POSTGRES_PASSWORD` through the environment.
- Supply different `LYRA_AGENT_API_KEY` and `LYRA_ADMIN_API_KEY` values.
- Provide Redis and a populated PostGIS database.
- Persist `/lyra_data` across API and worker restarts.

Use the generated [configuration reference](../../reference/generated/configuration/)
for exact fields, defaults, constraints, and environment ownership.

## Process order

Start Redis and PostGIS first. Start the API and wait for `/ready`; initial API
startup captures enabled plugin sources, validates manifests and routing, and
publishes a derived snapshot. Start workers only after readiness; they verify
the configuration fingerprint and source hashes, then install private copies
of the exact sources prepared by the API. The API does not import plugin code.

Each worker launcher receives a name from `[workers.<name>]`. That table controls
queues and concurrency; optional paths default below `/lyra_data`. Every metric
executes through `lyra.run_metric` on its server-assigned queue.

Before loading plugins or starting Celery, each worker opens a temporary database
connection and executes `SELECT 1` with its worker pool configuration. A failed
probe terminates startup so the process supervisor can retry it. Engines used by
metric execution are still created inside worker processes; a database outage
after startup is recorded as a retryable `database_unavailable` job failure.

## State and files

```text
/lyra_data/
  config/lyra.toml
  secrets/service-account.json
  cache/jobs/
  plugins/catalog/startup.json
  plugins/catalog/sources/
  plugins/runners/
  logs/
```

The catalog descriptor, captured sources, worker installs, and job caches are
derived artifacts. Do not edit or transfer them as configuration. Run one API
catalog writer per shared volume; multiple API replicas sharing this directory
are unsupported. A failed startup marks the descriptor unavailable, so workers
cannot silently reuse a previous catalog.

## Plugin configuration

```toml
[[plugins.repos]]
id = "analysis"
source = "owner/plugin-repository"
ref = "main"
enabled = true

[plugins.repos.routing]
expensive_metric = "batch"
```

Git refs accept branches, tags, and commit IDs. Omitting `ref` selects the default
branch. Branches and tags resolve again at each API startup; use full commit IDs
for reproducible source versions. Exact dependency reproducibility also requires
controlling package dependencies and the runtime image.

Unspecified metrics use `plugins.default_queue`. An enabled repository's routing
overrides must name metrics present in its manifest. Disabled repositories are
not fetched or installed; their overrides are retained and checked when enabled.
All configured queues must appear in `plugins.allowed_queues`.

## Public URL and reverse proxy

`api.public_base_url` must be the externally reachable HTTPS URL because Lyra
uses it for authenticated result handoffs. It may include a path prefix but not
credentials, query, or fragment. Loopback HTTP is accepted only for local
development.

Trust forwarded headers only from narrow proxy IPs or CIDRs in
`api.forwarded_allow_ips`. Preserve `Host`, `Authorization`,
`X-Forwarded-Proto`, and `X-Forwarded-For`. Never use a wildcard when untrusted
clients can reach the application port.

When MCP is enabled, the external endpoint is the configured mount path with a
trailing slash. It uses the agent key and exposes no admin operations. The
generated [MCP reference](../../reference/generated/mcp/) is authoritative for
tool contracts.

## Validate and deploy

```bash
uv run lyra-admin config validate ./lyra_data/config/lyra.toml
uv run lyra-admin --json config validate ./lyra_data/config/lyra.toml
```

Validation is offline and does not need credentials or server dependencies. It
checks TOML syntax, schema, repository declarations, and queue relationships;
it does not contact services, fetch manifests, import plugins, or install packages.

To reproduce a deployment, copy the TOML, provision the referenced external
secrets and services, and start the same runtime image. Adjust host-specific
paths and endpoints as needed. No first-run registration or interactive setup
is required. Local development sources must be available to the API; workers
consume the shared captured snapshot.

## Updates

1. Stop new submissions at your proxy or deployment boundary.
2. Drain all queued, reserved, and running jobs before changing routing or plugins.
3. Stop the API and every worker pool using your process supervisor or Compose.
4. Edit and validate the authoritative TOML.
5. Start the API and wait for readiness, then start every worker pool.
6. Inspect worker and queue coverage before reopening submissions.

For Compose, after draining, `docker compose -f docker/docker-compose-dev.yml down`
followed by `docker compose -f docker/docker-compose-dev.yml up -d --build` recreates
all processes and reapplies the API readiness dependency. Preserve the data volume.
Do not rely on `docker compose restart` to rerun dependency readiness gates.

File edits have no effect on a running process. Config summaries describe the
loaded configuration. There is no hot reload, config writer, config history, or
runtime plugin mutation API. Worker installation or import failures stop that
worker; inspect its logs and correct the package or config before restarting.
Invalid TOML or missing credentials stop startup. Plugin catalog failures leave
liveness and admin diagnostics available, but readiness and new submissions fail.
After correcting such failures, restart the full API/worker deployment; request
traffic never triggers a catalog retry.
