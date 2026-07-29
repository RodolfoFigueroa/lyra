# Step 03: Extract the PostgreSQL adapter into the SDK

Status: `complete`

Depends on: [Step 02: Make the SDK database interface authoritative](02-sdk-database-interface.md)

## Objective

Create one optional SDK PostgreSQL implementation of `LyraDB` and make Lyra
workers use it. This step is a behavior-preserving ownership move; query
hardening is reserved for Step 04.

## Current state

After Step 02, `LyraDB` is authoritative in the SDK, but its concrete
implementation still lives at `lyra_app/db/client.py` as `LyraDBImplicit`.
That class uses `load_geometries_from_bounds` from
`lyra_app/loaders/db.py`. Plugin projects installing only `lyra-sdk` cannot
instantiate the production implementation.

## Required implementation

### Optional SDK package surface

- Add an optional SDK extra named `postgres`.
- Declare all direct runtime dependencies required by the adapter, including
  SQLAlchemy, Psycopg, and GeoPandas. Do not rely on dependencies arriving
  transitively through `lyra-utils`.
- Add a module such as `lyra.sdk.postgres` containing `PostgresLyraDB`.
- Do not import that module or its optional dependencies during a plain
  `import lyra.sdk`.
- Give missing-extra imports a concise installation-oriented error only when
  the PostgreSQL module is explicitly imported or used.

### Adapter ownership

- Move the implementation of the three `LyraDB` methods into
  `PostgresLyraDB`.
- `PostgresLyraDB` accepts an existing synchronous SQLAlchemy engine and never
  disposes it.
- Move the private bounds-query helper needed exclusively by this adapter into
  the optional SDK module or a private sibling module.
- Rename the implementation; do not retain `Implicit` terminology.
- Keep each method's current connection scope and returned GeoDataFrame
  behavior unchanged in this step.

### Application migration

- Make `lyra-app` declare the SDK PostgreSQL extra as a direct dependency.
- Update worker context construction to instantiate `PostgresLyraDB` with the
  process-owned worker engine.
- Remove `lyra_app/db/client.py` once no callers remain.
- Remove the old helper from `lyra_app/loaders/db.py` if it has no application
  callers after extraction.
- Keep `ApplicationDatabaseRuntime`, worker engine creation, configuration, and
  API-only spatial loaders in `lyra_app`.

### Packaging verification

Two installation modes must be tested:

1. core `lyra-sdk`, where `LyraDB` and `StubLyraDB` import without database
   packages;
2. `lyra-sdk[postgres]`, where `PostgresLyraDB` imports and executes.

The application wheel must pull in everything it imports directly.

## Compatibility requirements

- Worker plugins continue receiving a non-null `LyraDB`.
- Production queries, pool ownership, statement deadlines, and result shapes
  remain unchanged.
- The adapter does not read Lyra application configuration.
- The adapter does not create an engine or accept a password-bearing URL.
- Step 01 integration tests must pass against both the old baseline behavior
  and the newly located implementation.

## Expected files

Likely areas include:

- `packages/lyra_sdk/pyproject.toml`;
- new modules under `packages/lyra_sdk/src/lyra/sdk/`;
- root `pyproject.toml` and `uv.lock`;
- `lyra_app/worker.py`;
- `lyra_app/db/client.py`;
- `lyra_app/loaders/db.py`;
- SDK and worker tests;
- installed-distribution smoke tests.

## Acceptance criteria

- There is exactly one concrete PostgreSQL `LyraDB` implementation.
- Lyra workers and future local callers can import that same implementation.
- Core SDK imports remain dependency-light.
- Engine ownership remains with the worker runtime.
- No application module is imported by `lyra.sdk.postgres`.
- Existing real PostGIS integration behavior is unchanged.

## Verification

Run the required Python checks, plus:

- Step 01 PostGIS integration tests;
- worker context and runner tests;
- SDK import tests with and without the `postgres` extra;
- `uv lock --check` after updating the lockfile;
- builds of `lyra-sdk` and `lyra-app`;
- isolated wheel smoke tests for core SDK, SDK with PostgreSQL support, and the
  application;
- the full suite with coverage because package ownership changes broadly.

## Non-goals

- Do not alter SQL composition or runtime argument validation.
- Do not add an engine-owning connection manager.
- Do not change error classification.
- Do not add read-only session defaults.
- Do not add `LocalRunContext`.
- Do not add a database contract check.

## Implementation record

Complete this section when the step is implemented.

- Date: 2026-07-28
- Material changes: Added the optional `lyra-sdk[postgres]` extra with direct
  GeoPandas, Psycopg, and SQLAlchemy dependencies; moved the sole concrete
  database implementation and its private bounds loader into
  `lyra.sdk.postgres.PostgresLyraDB` with an installation-oriented missing-extra
  error; migrated workers and the PostGIS characterization suite to the shared
  engine-injected adapter; removed `lyra_app/db/client.py` and the extracted
  application helper; declared the SDK PostgreSQL extra as an application
  dependency; updated the lockfile; added SDK coverage for optional-import
  behavior, interface compatibility, and engine ownership; strengthened the
  worker context assertion; and taught release metadata validation to recognize
  minimum-version requirements that include extras.
- Verification: `uv run ruff format .`, `uv run ruff check .`, `uv run ty
  check`, `uv lock --check`, and `uv run python -m build_scripts.release
  validate` passed; focused SDK database and worker tests passed (46 tests);
  release pipeline tests passed (10 tests); the integration selection without
  `LYRA_TEST_POSTGIS_URL` safely skipped all 5 tests; the Step 01 suite passed
  against the documented pinned disposable PostGIS service (5 tests); both
  `lyra-sdk` and `lyra-app` built as wheels and source distributions; isolated
  wheel smoke tests passed for core `lyra-sdk`, `lyra-sdk[postgres]`, and
  `lyra-app` with its local workspace wheels; and the full coverage command
  passed against PostGIS (717 tests, 86% total coverage) and regenerated
  `coverage.xml`.
- Deviations or follow-up notes: None. The live PostGIS and full-suite commands
  ran outside the restricted command sandbox because it blocks localhost
  database access and has a known cross-thread event-loop limitation. No
  implementation decision changed a later handoff assumption.
