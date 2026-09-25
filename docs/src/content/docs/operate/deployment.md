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

## Build the deployment image

The root image contains the framework. A deployment project adds your selected
plugins and owns its dependency lockfile. The repository includes a complete
[deployment example](https://github.com/RodolfoFigueroa/lyra/tree/main/examples/lyra-deployment)
with production and editable development targets. Use the repository's
`examples/lyra-deployment/README.md` for commands and checkout layout.

Install all dependencies during the image build using `uv sync --locked --no-dev
--no-editable`. Run API and every worker from the same resulting image. Runtime
containers need neither Git nor uv. Pin Git dependencies to commits and commit the
deployment lockfile; changing a package requires rebuilding the image.

## Startup and files

Start Redis and PostGIS first. API and workers then start independently. Each reads
installed distribution metadata and manifests. The API publishes a catalog without
importing plugin Python code; workers import factories for plugins serving their
queues and check the live definition against its manifest.

Each worker launcher receives a name from `[workers.<name>]`, which controls queues
and concurrency. Before loading plugins or starting RQ, each worker probes
PostGIS with `SELECT 1`. A failed probe terminates startup. Metric execution engines
are created inside worker processes; later database outages produce retryable
`database_unavailable` job failures.

```text
/lyra_data/
  config/lyra.toml
  secrets/service-account.json
  cache/jobs/
  logs/
```

There is no shared catalog directory or worker installation directory. Plugin
manifests live in the image's Python environment below
`share/lyra/plugins/<normalized-distribution>/lyra.plugin.json`.

## Plugin configuration

Configuration uses `schema_version = 4`:

```toml
[[plugins.installed]]
distribution = "my-plugin"
enabled = true

[plugins.installed.routing]
expensive_metric = "batch"
```

Unspecified metrics use `plugins.default_queue`. An enabled plugin's routing
must name metrics in its manifest. All queues must appear in
`plugins.allowed_queues`. Disabled distributions are not loaded.

For development, use editable installs, mount each checkout into API and worker
containers at the path used during installation, and set an absolute
`manifest_path` on its installed-plugin entry. The development Compose override in
the example supports independent checkouts through named build contexts. Python
source edits need manual worker restarts; contract edits also need manifest
regeneration and an API restart. Dependency and version edits need a lock update
and image rebuild. Keep the container environment separate from host environments.

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
checks TOML syntax, schema, distribution declarations, and queue relationships;
it does not contact services, read manifests, import plugins, or install packages.

To reproduce a deployment, copy the TOML, provision the referenced external
secrets and services, and start the same runtime image. Adjust host-specific
paths and endpoints as needed. No first-run registration or interactive setup is required.

## Updates

1. Stop new submissions at your proxy or deployment boundary.
2. Drain all queued, reserved, and running jobs before changing routing or plugins.
3. Stop the API and every worker pool using your process supervisor or Compose.
4. Update and validate the TOML; update locks and rebuild for package changes.
5. Recreate API and workers with the same image and config, then wait for readiness.
6. Inspect worker and queue coverage before reopening submissions.

Use `docker compose up -d --build --force-recreate` in the deployment example after
draining. Preserve the data volume. For development source-only edits, manual
`docker compose restart` is sufficient; there is no automatic reload by default.

File edits have no effect on a running process. Config summaries describe the
loaded configuration. There is no hot reload, config writer, config history, or
runtime plugin mutation API. Worker import or manifest failures stop that
worker; inspect its logs and correct the package or config before restarting.
Invalid TOML or missing credentials stop startup. Plugin catalog failures leave
liveness and admin diagnostics available, but readiness and new submissions fail.
After correcting such failures, restart the full API/worker deployment; request
traffic never triggers a catalog retry.
