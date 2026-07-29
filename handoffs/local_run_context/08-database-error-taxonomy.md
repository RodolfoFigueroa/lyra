# Step 08: Introduce a precise database error taxonomy

Status: `complete`

Depends on: [Step 07: Correct spatial executor cancellation](07-spatial-executor-cancellation.md)

## Objective

Replace broad SQLAlchemy exception handling with stable SDK-owned database
errors and SQLSTATE-aware classification. Only genuinely transient conditions
should become retryable database-outage responses.

## Current state

Application spatial resolution converts every `SQLAlchemyError` into
`SpatialInputResolutionUnavailableError`, so missing tables, invalid columns,
and programming mistakes can appear transient. The worker classifier treats
every `OperationalError` as unavailable and separately recognizes connection
invalidation, pool timeout, and SQLSTATE `57014`.

These rules are convenient but too broad once the adapter is a reusable SDK
component and local authors connect directly.

## Required implementation

### SDK error hierarchy

Define a dependency-light public hierarchy in the SDK:

- `LyraDatabaseError`: base for database-service failures;
- `DatabaseNotConfiguredError`: retain the Step 02 strict-stub error under this
  hierarchy;
- `DatabaseUnavailableError`: connection, pool-capacity, or server-availability
  failure;
- `DatabaseQueryTimeoutError`: statement deadline or query cancellation;
- `DatabaseQueryError`: non-transient database execution or schema/programming
  failure.

Invalid method arguments continue to raise `ValueError` before database access.
Preserve original driver/SQLAlchemy exceptions as causes, but do not expose
credentials, complete SQL parameters, or internal database messages in public
result payloads.

### PostgreSQL classifier

Centralize SQLAlchemy/Psycopg classification in the optional PostgreSQL module.
Use, in order:

- explicit SDK database errors;
- SQLAlchemy pool timeout;
- connection invalidation;
- PostgreSQL SQLSTATE class and condition;
- narrowly justified driver exceptions without SQLSTATE.

Classify connection exceptions and server shutdown as unavailable. Classify
query cancellation/statement timeout separately. Treat authentication,
insufficient privilege, undefined table/column, syntax, and other deterministic
programming failures as non-transient query errors. Add narrowly documented
handling for deadlock or serialization states only if they are possible in the
read-only workloads.

Do not classify by localized message text.

### Adapter and application translation

- `PostgresLyraDB` translates backend exceptions at its public boundary.
- Application-only spatial loaders use the same classifier rather than a
  separate broad catch.
- Remove or reduce `lyra_app.db.connection.is_database_unavailable_error` so
  there is one source of classification truth.
- Spatial-input validation distinguishes caller validation from unavailable
  infrastructure and non-transient database query failure.
- API, MCP, and worker layers translate SDK errors consistently.

Expected external behavior:

- unavailable database: retryable service failure;
- database query timeout: explicit timeout failure with documented retryability;
- invalid plugin/local arguments: caller error;
- schema or SQL programming defect: non-retryable internal/query failure,
  prominently logged with its exception chain;
- local execution: the typed SDK exception reaches the plugin author.

Do not automatically retry any operation.

### Tests

Cover representative synthetic SQLSTATE values and real integration failures:

- connection refused or terminated;
- pool exhaustion;
- statement timeout;
- insufficient privilege;
- undefined table and column;
- invalid syntax;
- connection invalidation;
- already-wrapped SDK errors;
- safe public serialization and preserved exception chaining.

Update route, MCP, and worker tests to assert retryability and error codes, not
driver-specific message text.

## Expected files

Likely areas include:

- SDK database/error modules and exports;
- optional SDK PostgreSQL adapter/classifier;
- `lyra_app/db/connection.py`;
- `lyra_app/spatial_inputs.py`;
- jobs and met-zone routes;
- MCP backend error mapping;
- worker failure normalization;
- documentation and tests.

## Acceptance criteria

- One SQLSTATE-aware classifier serves adapter and application loaders.
- Deterministic database defects are not reported as transient outages.
- Only SDK-owned errors cross the public `PostgresLyraDB` boundary.
- Worker, HTTP, MCP, and local callers receive consistent categories.
- Secrets and bound values are absent from public error payloads.
- No retry loop is introduced.

## Verification

Run the required Python checks, plus:

- classifier unit tests;
- PostGIS failure integration tests;
- worker, route, job-submission, MCP, and SDK tests;
- generated API/docs checks if error documentation changes;
- the full suite with coverage.

## Non-goals

- No schema compatibility probe.
- No retry policy.
- No observability platform or database metrics subsystem.
- No local context yet.

## Implementation record

Complete this section when the step is implemented.

- Date: 2026-07-28
- Material changes: Added a dependency-light public SDK database hierarchy with
  stable codes, retryability, and safe messages; made
  `DatabaseNotConfiguredError` inherit from it; and added one optional
  PostgreSQL classifier that prioritizes SDK errors, pool exhaustion,
  invalidation, SQLSTATE, and narrow no-SQLSTATE Psycopg connection failures.
  Wrapped every `PostgresLyraDB` operation at its public boundary while
  preserving argument validation and exception causes. Reused the classifier
  for application spatial and async lookup failures, removed the application
  duplicate availability classifier, and normalized HTTP, MCP, and worker
  responses for unavailable, timeout, and non-transient query failures without
  exposing SQL text, bound values, credentials, or backend messages. Added
  operator/plugin documentation and generated-reference entries for the public
  errors.
- Verification: `uv run ruff format .`, `uv run ruff check .`, and `uv run ty
  check` passed. Classifier, SDK, spatial-loader, runtime, route,
  job-submission, MCP, worker, and PostGIS coverage passed with `uv run pytest
  tests/test_database_errors.py tests/test_sdk_database.py
  tests/test_spatial_loaders.py tests/test_database_runtime.py
  tests/test_jobs_route.py tests/test_mcp_server.py tests/test_runner.py
  tests/test_postgis_integration.py -q` (258 passed, 18 live-PostGIS tests
  skipped). Generated documentation passed with `npm run generate --prefix
  docs`, and documentation/release checks passed with `uv run pytest
  tests/test_docs_contract.py tests/test_release_pipeline.py -q` (18 passed).
  The full coverage command `uv run pytest --cov=lyra_app --cov=lyra
  --cov-report=term-missing --cov-report=xml` passed (791 tests, 18
  live-PostGIS tests skipped because `LYRA_TEST_POSTGIS_URL` was not
  configured, 86% total coverage) and regenerated `coverage.xml`.
- Deviations or follow-up notes: No design deviations. Real PostGIS failure
  cases for statement timeout, privilege, undefined table/column, invalid
  syntax, pool exhaustion, authentication, and terminated connections were
  added but could not execute locally without `LYRA_TEST_POSTGIS_URL`. No later
  handoff assumptions changed.
