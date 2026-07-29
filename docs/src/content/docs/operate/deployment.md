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
Query cancellation or statement timeout is reported separately as retryable
`database_query_timeout`. Authentication, privilege, missing schema objects,
invalid SQL, and other deterministic defects are non-retryable
`database_query_error` failures and retain their backend exception chain in
server logs. Lyra never retries these operations automatically, and public
result payloads do not include SQL parameters or internal database messages.

## Read-only database role

Use dedicated login roles for the application runtime and vetted local authors;
never reuse the database owner or migration role. No reader role may own the
database, data schema, tables, or views. Grant only database `CONNECT`,
target-schema `USAGE`, `SELECT` on required tables or views, and `EXECUTE` on
required functions. Do not grant `CREATE`, `INSERT`, `UPDATE`, `DELETE`,
`TRUNCATE`, trigger, or ownership privileges.

The following is an operator template, not an application migration. Run it as a
database administrator, replace the identifiers, and supply credentials through
your secret manager rather than placing a password in this file or startup
automation:

```sql
CREATE ROLE lyra_runtime LOGIN;

GRANT CONNECT ON DATABASE lyra TO lyra_runtime;
GRANT USAGE ON SCHEMA lyra_data TO lyra_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA lyra_data TO lyra_runtime;
GRANT EXECUTE ON FUNCTION lyra_data.required_reader_function(integer)
    TO lyra_runtime;

ALTER DEFAULT PRIVILEGES
    FOR ROLE lyra_data_owner
    IN SCHEMA lyra_data
    GRANT SELECT ON TABLES TO lyra_runtime;

REVOKE CREATE ON DATABASE lyra FROM lyra_runtime;
REVOKE CREATE ON SCHEMA lyra_data FROM lyra_runtime;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

ALTER ROLE lyra_runtime SET default_transaction_read_only = on;
```

The external loader must create future tables and views as
`lyra_data_owner` (or the actual data-owning role named in
`ALTER DEFAULT PRIVILEGES`). Audit direct and inherited grants so
`lyra_runtime` has no write, truncate, trigger, or ownership capability. A
database-level read-only default may be used when every connection to that
database is a reader; otherwise keep the role-level default shown above.

Create an equivalent, separately revocable role for each author or approved
author group and narrow its table, view, and function grants to actual plugin
needs. Protect password-bearing URLs in a secret manager, rotate them, and
revoke login or grants when access ends. Credentials must never appear in
source control, shell history, logs, screenshots, or issue reports. Configure
the approved schema explicitly rather than relying on a broad search path.

Lyra also sets every data-engine session to read-only and assigns stable
`application_name` values (`lyra-api`, `lyra-spatial`, `lyra-worker`, and
`lyra-probe`); the local SDK uses `lyra-sdk-local`. These settings are
low-cardinality observability aids, not authorization or a substitute for
database privileges. Trusted authors, read-only session settings, and
`LocalRunContext` do not form a security boundary. Application startup never creates or
changes roles, grants, schemas, or data objects, and local access must treat the
shared database as real data.

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
