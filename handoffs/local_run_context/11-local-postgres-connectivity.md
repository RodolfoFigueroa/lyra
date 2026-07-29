# Step 11: Connect `LocalRunContext` to Postgres/PostGIS

Status: `planned`

Depends on:

- [Step 03: Extract the Postgres Adapter into the SDK](03-postgres-adapter-extraction.md)
- [Step 04: Harden Spatial Query Construction](04-spatial-query-hardening.md)
- [Step 05: Add Explicit Read-Only Connection Profiles](05-read-only-connection-profiles.md)
- [Step 07: Make Spatial Executor Cancellation Capacity-Safe](07-spatial-executor-cancellation.md)
- [Step 08: Define a Stable Database Error Taxonomy](08-database-error-taxonomy.md)
- [Step 10: Add `LocalRunContext`](10-local-run-context.md)

## Objective

Provide an explicit, resource-safe way for trusted plugin authors to connect `LocalRunContext` directly to a shared Postgres/PostGIS instance and execute the same database reads as production Lyra.

This is the first-iteration trusted-author workflow. It intentionally does not add a service proxy, rate limiting, strict authentication, or a database contract/version check.

## Expected starting state

Steps 01 through 10 are complete. `lyra.sdk.postgres.PostgresLyraDB` is the
single engine-injected adapter used by the application, connection profiles are
explicit and read-only by default, and `LocalRunContext` works with its strict
stub or a caller-injected database. No SDK API owns an engine created from a
database URL.

## Public API

### 1. Engine-owning Postgres connector

Add `connect_postgres` to `lyra.sdk.postgres` as a context manager.

It must:

- accept a Postgres URL as a string or the SQLAlchemy URL type already supported by the adapter;
- accept a keyword-only `schema` argument whose default is the SDK adapter's
  shared schema constant from Step 04 (the backward-compatible schema is
  `public` unless Step 04 records an approved deviation);
- construct the SDK's `PostgresLyraDB`;
- yield that database interface;
- perform an eager connectivity probe before yielding;
- close and dispose every owned resource on normal exit and exceptional exit; and
- use the local-development connection profile defined below.

The connectivity probe must be limited to verifying that a database connection can be established, such as `SELECT 1`. It must not inspect schema versions, migration state, table shape, readiness metadata, or API compatibility.

### 2. `LocalRunContext.connect_postgres`

Add a class-level context manager named `LocalRunContext.connect_postgres`.

It must accept the normal local-context constructor inputs plus:

- the database URL;
- the configured schema; and
- narrowly scoped optional connection-profile overrides already supported by `connect_postgres`.

It must compose the SDK Postgres connector and yield a fully constructed `LocalRunContext` whose `db` is the live `PostgresLyraDB`.

Resource ownership must be unambiguous:

- the class-level context manager owns the database engine and adapter lifecycle;
- the yielded `LocalRunContext` does not outlive that manager;
- exiting the manager disposes the owned engine even when plugin execution raises; and
- a plain `LocalRunContext` constructed with an injected `db` never disposes caller-owned resources.

Document that using the context or database after the manager exits is unsupported.

### 3. Optional-dependency isolation

The core SDK and `lyra.sdk.local` must remain importable without the Postgres extra installed.

Calling `LocalRunContext.connect_postgres` without the optional Postgres
dependencies must raise `ImportError` with a concise message naming the
`lyra-sdk[postgres]` extra. Achieve this with a lazy import or another boundary
that does not import database dependencies during ordinary local-context use.

Do not catch and misreport a connection failure as a missing-extra failure.

## Local connection profile

Use explicit defaults suitable for a plugin author running one local execution:

- read-only transaction behavior enabled by default;
- `application_name` fixed to `lyra-sdk-local`;
- a pool size of one;
- no overflow connections;
- a five-second connect timeout;
- a five-second pool-acquisition timeout;
- a 300,000-millisecond statement timeout;
- a 900-second pool recycle interval; and
- URL query options preserved rather than overwritten.

Allow keyword-only overrides named `connect_timeout_seconds`,
`pool_timeout_seconds`, `statement_timeout_ms`, and `pool_recycle_seconds`.
Validate them as positive numeric values before constructing an engine. Pool
size, overflow, read-only behavior, and `application_name` are fixed in this
iteration. Do not expose arbitrary engine keyword dictionaries.

The direct database URL is sensitive. Exception messages, representations, and logs must redact passwords and other embedded credentials.

## Transaction and query semantics

Use the same `PostgresLyraDB` implementation and hardened query construction used by the application.

Each database method call continues to execute in its own read transaction. Multiple calls from one plugin execution are not guaranteed to observe one shared snapshot. Do not introduce a session, unit-of-work, or multi-call transaction API in this step.

Read-only defaults are defense in depth, not a substitute for database permissions. Documentation completed in the next step must recommend a dedicated database role with only the required schema/table/function access.

## Expected file areas

The implementer should confirm exact paths before editing. The likely areas are:

- `packages/lyra_sdk/src/lyra/sdk/postgres.py`;
- `packages/lyra_sdk/src/lyra/sdk/local.py`;
- SDK exports and optional-dependency metadata;
- SDK unit tests; and
- the PostGIS integration-test suite introduced in Step 01.

## Tests

Add unit tests proving:

- the core SDK and `LocalRunContext` import without the Postgres extra;
- calling the live connector without its extra produces the intended installation error;
- malformed configuration fails clearly without leaking credentials;
- URL query parameters are retained;
- local profile defaults are applied;
- resources are disposed after normal completion;
- resources are disposed when construction, probing, or plugin execution raises;
- an injected database remains caller-owned; and
- connection and query exceptions retain the database error taxonomy from Step 08.

Add marked PostGIS integration tests proving:

- the eager connectivity probe succeeds against the test service;
- a locally connected context can call every `LyraDB` read method;
- the configured schema is honored;
- the session is read-only by default;
- committed writes are rejected for the read-only integration role;
- spatial query results match those returned through the production adapter path; and
- no database resource remains checked out after the context manager exits.

Do not add a schema/version compatibility assertion to these tests.

## Acceptance criteria

- A trusted plugin author can execute a plugin against the shared Postgres/PostGIS instance without running Lyra.
- The local path and application path use the same SDK Postgres adapter.
- The simple local path opens no more than one pooled connection by default.
- The connection is read-only by default and preserves explicitly supplied URL options.
- Owned resources are deterministically disposed.
- Credentials are not exposed by normal errors or representations.
- The core SDK remains usable without database dependencies installed.
- All added and affected unit and integration tests pass.
- Required repository formatting, linting, type-checking, and test checks pass.

## Non-goals

- Database schema/version contract checks or migration checks.
- A backend database proxy.
- Rate limits, quotas, or strict author authentication.
- Snapshot consistency across multiple `LyraDB` calls.
- Database writes.
- Automatic retries.
- Streaming results or result-size limits.
- A general plugin execution runner or CLI.

## Verification

Run:

1. `uv run ruff format .`
2. `uv run ruff check .`
3. `uv run ty check`
4. the SDK connector and local-context unit tests;
5. the marked PostGIS integration tests;
6. package-isolation tests for the core SDK and Postgres extra; and
7. the full test suite with the repository's documented coverage command.

The full test suite must generate `coverage.xml`.

## Implementation record

Complete this section when the step is implemented.

- Date:
- Material changes:
- Verification:
- Deviations or follow-up notes:
