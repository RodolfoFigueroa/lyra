---
title: Deployment
description: Deploy API and worker containers with explicit config, secrets, state, and routing.
---

Lyra separates deployment-owned config and secrets from Lyra-owned plugin state.
API and workers read the same TOML and environment variables and share one
writable `/lyra_data` volume.

## Required deployment inputs

- Mount `lyra.toml` at `/lyra_data/config/lyra.toml` read-only.
- Mount the Earth Engine key at its configured absolute path read-only.
- Supply all `LYRA_POSTGRES_*` variables.
- Supply different `LYRA_AGENT_API_KEY` and `LYRA_ADMIN_API_KEY` values.
- Provide Redis and a populated PostGIS database.
- Persist `/lyra_data` across API and worker restarts.

Use the generated [configuration reference](../../reference/generated/configuration/)
for exact fields, defaults, constraints, and environment ownership.

## Process order

Start Redis and PostGIS first. Start the API and wait for `/ready`; initial API
startup validates plugin sources, creates Lyra-owned plugin state, and assigns
missing routes. Start workers only after readiness so their installs use
committed state and routing.

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
  state/plugins.toml
  cache/jobs/
  plugins/catalog/
  plugins/runners/
  logs/
```

Do not host-mount `state/plugins.toml`. The API creates and atomically updates
it. `plugins.initial_repos` applies only when this state does not exist;
subsequent changes use admin APIs.

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

## Updates

Plugin changes are explicit: update a source, refresh the catalog, inspect
routing, and restart recommended workers. Application deployments should drain
or replace workers deliberately; running plugin code observes cancellation only
when it calls `context.check_cancelled()`.
