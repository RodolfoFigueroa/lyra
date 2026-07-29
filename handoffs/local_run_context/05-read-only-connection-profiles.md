# Step 05: Read-only connection profiles

Status: `complete`

Depends on: [Step 04: Harden spatial query construction](04-spatial-query-hardening.md)

## Objective

Make the read-only database contract explicit in engine construction,
deployment guidance, and integration tests. Distinguish API, spatial, worker,
and future local-development workloads without giving the SDK responsibility
for application configuration.

## Current state

`DatabasePoolConfig` already separates API, spatial, and worker pool limits and
deadlines. Engine options set connect, pool, recycle, pre-ping, and statement
timeouts. The repository describes `LyraDB` as read-only, but it neither sets
read-only transaction defaults nor verifies that deployment roles lack write
privileges. Connections also lack workload-specific PostgreSQL
`application_name` values.

## Required implementation

### Application engine profiles

- Require every engine factory call to identify its workload: API, spatial,
  worker, probe, integration, or local development as applicable.
- Set a stable, low-cardinality `application_name` visible in PostgreSQL
  activity views. Do not include hostnames, job IDs, plugin names, or other
  unbounded values.
- Set `default_transaction_read_only=on` for all Lyra data engines in this
  series.
- Preserve existing connection, pool, recycle, pre-ping, and statement
  deadlines.
- Keep async and synchronous engines on the supported Psycopg 3 SQLAlchemy
  dialect.
- Ensure URL-supplied standard libpq options, including TLS options, are not
  accidentally discarded when engine-specific options are added.
- Redact parameters and password-bearing URLs from engine/error logging where
  SQLAlchemy configuration supports it.

The application continues to build its URL from validated configuration. The
optional SDK adapter still does not read `LyraConfig`.

### Database role guidance

Update deployment documentation with a concrete least-privilege contract:

- the runtime/local role is not a database, schema, table, or view owner;
- it receives database `CONNECT`, target-schema `USAGE`, and `SELECT` on the
  required tables or views;
- it lacks `CREATE`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, trigger, and
  ownership privileges;
- default privileges grant selection on future data objects when the external
  data-loading owner creates them;
- public schema creation is revoked where appropriate;
- role or database defaults enable read-only transactions.

Do not make application startup create or mutate roles. Central database
provisioning remains an operator responsibility. Provide commands as an
operator template rather than a credential-bearing automated migration.

### Verification tests

Extend the disposable PostGIS environment with separate owner and read-only test
roles. Verify that the runtime role:

- can execute every supported query;
- reports read-only transactions;
- cannot insert, update, delete, create a persistent table, or change the data
  schema;
- is identified by the intended workload application name;
- does not leak its password in a deliberately raised connection error.

Keep the role fixture test-only and deterministic.

## Transaction policy

Continue using connection context managers for read operations. Do not switch
the query API to autocommit merely to avoid rollback-on-return. If a future
feature introduces writes, it must use a separate writer role and explicit
`engine.begin()` transaction; it must not weaken these read-only profiles.

## Expected files

Likely areas include:

- `lyra_app/db/connection.py`;
- application configuration and tests if workload options require new fields;
- optional SDK PostgreSQL connection-option helpers, without adding the local
  engine manager yet;
- PostGIS role fixtures and integration tests;
- deployment and runbook documentation.

## Acceptance criteria

- Every Lyra data engine has an explicit workload name and read-only default.
- Pool and deadline behavior remains unchanged.
- Disposable integration roles prove both permitted reads and rejected writes.
- Deployment documentation defines the required central role grants.
- Plain SDK imports remain independent of database dependencies.
- No runtime role provisioning or schema contract check is added.

## Verification

Run the required Python checks, plus:

- engine-option unit tests for sync and async engines;
- read-only PostGIS integration tests;
- configuration and documentation checks;
- worker startup/probe tests;
- full suite with coverage because connection construction affects all
  database workloads.

## Non-goals

- No per-author roles, credential issuance, rotation automation, or rate limits.
- No database write API.
- No schema-version or table compatibility probe.
- No local connection manager yet.
- No retry implementation.

## Implementation record

Complete this section when the step is implemented.

- Date: 2026-07-28
- Material changes: Added a shared optional-SDK PostgreSQL profile helper with
  fixed low-cardinality API, spatial, worker, probe, integration, and future
  local workload names; made workload selection mandatory at application engine
  factories; applied read-only transaction defaults and preserved URL-supplied
  libpq/TLS options while retaining all existing connection, pool, recycle,
  pre-ping, and statement deadlines; enabled SQLAlchemy parameter hiding;
  assigned distinct API, spatial, worker, and startup-probe profiles; extended
  the disposable PostGIS fixture with isolated owner and login-capable runtime
  roles, least-privilege grants, role defaults, and deterministic cleanup; added
  live assertions for every supported read, read-only/session identity,
  privilege denial, rejected DML/DDL, pool return, and password redaction; and
  documented an operator-owned least-privilege SQL template plus ongoing role
  auditing guidance.
- Verification: `uv run ruff format .`, `uv run ruff check .`, and `uv run ty
  check` passed; focused engine, configuration, worker/probe, SDK database, and
  documentation contract tests passed (49 tests); the live PostGIS 17 / PostGIS
  3.5 integration suite passed (13 tests); `npm run generate --prefix docs`,
  `npm run check --prefix docs`, `npm run build --prefix docs`, and `npm run
  check:links --prefix docs` passed; and the full live-database coverage command
  passed (762 tests, 86% total coverage) and regenerated `coverage.xml`.
- Deviations or follow-up notes: None. The shared profile helper includes the
  reserved `lyra-sdk-local` identity for Step 11 but does not create a local
  engine or connection manager. No later handoff assumption changed.
