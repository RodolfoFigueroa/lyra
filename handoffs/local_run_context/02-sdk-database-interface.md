# Step 02: Make the SDK database interface authoritative

Status: `complete`

Depends on: [Step 01: PostGIS integration harness](01-postgis-integration-harness.md)

## Objective

Replace the generated `LyraDB` abstract class with a hand-maintained SDK
contract and add a strict database stub suitable for plugin development. Keep
the existing application-side PostgreSQL implementation in place for this step.

## Current state

`packages/lyra_sdk/src/lyra/sdk/db.py` is generated from
`lyra_app/db/client.py` by `build_scripts/generate_sdk.py`. A pre-commit hook
regenerates the file when the application implementation changes. As a result,
the backend implementation is the source of truth for a public SDK API.

The generated `LyraDB` also declares an engine-oriented constructor even though
engine ownership is not part of the plugin-facing database contract. Tests work
around the absence of a supported stub by casting an arbitrary object to
`LyraDB`.

## Required implementation

### Authoritative interface

- Convert `lyra.sdk.db.LyraDB` into a normal, hand-maintained abstract base
  class.
- Preserve the names, arguments, defaults, return annotations, and documented
  meaning of every public database method.
- Remove the engine constructor from the abstract interface. Concrete
  implementations decide their own construction requirements.
- Keep the class dependency-light: imports of GeoPandas, SQLAlchemy, and related
  types must remain typing-only.
- Keep `LyraDB` exported from `lyra.sdk` so existing metric annotations and
  imports remain valid.

An abstract base class is intentional for this series. Do not convert `LyraDB`
to a protocol in this step.

### Strict stub

- Add `DatabaseNotConfiguredError`, a clear SDK-owned runtime error describing
  which database operation was attempted and how a caller can supply a real or
  fake implementation.
- Add `StubLyraDB`, which implements every `LyraDB` method by raising that
  error.
- The stub must have no optional database dependencies and require no engine.
- Unexpected calls must never return empty frames or otherwise simulate
  success.
- Export the error and stub from the appropriate SDK modules and generated API
  reference inputs.

### Remove generation

- Remove the database-interface generator, its shell wrapper, its unit tests,
  and the local pre-commit hook.
- Update comments and documentation that describe `db.py` as generated.
- Do not remove unrelated build-generation infrastructure.

### Migrate internal tests

- Replace unsafe casted database sentinels used only to satisfy `RunContext`
  typing with `StubLyraDB`.
- Add SDK tests covering abstractness, stub behavior, method-specific error
  messages, and dependency-light imports.
- Confirm that the existing `LyraDBImplicit` application implementation still
  satisfies and subclasses the new interface.

## Compatibility requirements

- Metric code importing `LyraDB` or referring to `context.db` must continue to
  work.
- No method may become optional or nullable.
- This step may intentionally affect third-party subclasses that called the old
  abstract engine constructor. Document that the constructor was backend
  leakage and that concrete subclasses should own their initialization.
- Do not change runtime query behavior, connection creation, or worker context
  construction.

## Expected files

Likely areas include:

- `packages/lyra_sdk/src/lyra/sdk/db.py`;
- `packages/lyra_sdk/src/lyra/sdk/__init__.py`;
- `.pre-commit-config.yaml`;
- `build_scripts/generate_sdk.py` and `build_scripts/generate_sdk.sh`;
- `tests/test_generate_sdk.py`;
- `tests/test_sdk_plugin.py`;
- SDK API-reference generation inputs and relevant docs.

## Acceptance criteria

- `LyraDB` is defined and reviewed directly in the SDK.
- No database-interface generator or pre-commit hook remains.
- `StubLyraDB` is the standard strict non-database implementation.
- Importing the core SDK does not require GeoPandas, SQLAlchemy, or Psycopg.
- The application-side adapter still runs unchanged against the Step 01
  integration tests.
- Public database methods retain their existing signatures.

## Verification

Run the required formatting, linting, and type checks, plus:

- SDK plugin/context tests;
- application database adapter integration tests from Step 01;
- pre-commit over all files, because a hook is removed;
- distribution build and an isolated core-SDK import smoke test;
- the full suite with coverage because public SDK and build infrastructure
  changed.

## Non-goals

- Do not move `LyraDBImplicit` into the SDK yet.
- Do not add SDK database extras.
- Do not harden SQL construction.
- Do not add `LocalRunContext`.
- Do not add a database contract check.

## Implementation record

Complete this section when the step is implemented.

- Date: 2026-07-28
- Material changes: Replaced the generated SDK database class with a
  hand-maintained, dependency-light `LyraDB` abstract interface without an
  engine constructor; added and exported `DatabaseNotConfiguredError` and the
  strict `StubLyraDB`; migrated the SDK plugin test context from an unsafe cast
  to the stub; added SDK coverage for abstractness, application adapter
  compatibility, preserved method parameter contracts, method-specific stub
  failures, and imports with database packages blocked; removed the SDK
  database generator, wrapper, tests, and pre-commit hook; and added the new
  SDK symbols and constructor guidance to API-reference inputs and plugin
  documentation.
- Verification: `uv run ruff format .`, `uv run ruff check .`, and
  `uv run ty check` passed; `uv run pytest tests/test_sdk_database.py
  tests/test_sdk_plugin.py -q` passed (22 tests); the Step 01 PostGIS suite
  passed against a disposable pinned PostGIS container (5 tests), while the
  same selection without `LYRA_TEST_POSTGIS_URL` safely skipped all 5 tests;
  `uv run pre-commit run --all-files` passed; `uv build --package lyra-sdk`
  built the wheel and source distribution; an isolated Python 3.13 environment
  installed the core wheel and imported the public database symbols without
  loading GeoPandas, Psycopg, or SQLAlchemy;
  `npm run generate --prefix docs` generated reference entries for all three
  database symbols; and the full coverage command passed against the
  disposable PostGIS service (715 tests, 86% total coverage) and regenerated
  `coverage.xml`.
- Deviations or follow-up notes: None. The full suite was run outside the
  restricted command sandbox because that environment does not deliver
  asyncio cross-thread event-loop wakeups; the same suite completes normally
  without that sandbox restriction. No implementation decision changed a later
  handoff assumption.
