# Step 10: Add `LocalRunContext`

Status: `complete`

Depends on:

- [Step 02: Establish the SDK Database Interface and Stub](02-sdk-database-interface.md)
- [Step 08: Define a Stable Database Error Taxonomy](08-database-error-taxonomy.md)
- [Step 09: Share Run-Context Event Semantics](09-run-context-event-semantics.md)

## Objective

Add an SDK-owned `LocalRunContext` that lets plugin authors execute plugin callables directly without running Lyra or any backend service.

The default context must be deterministic, inspectable, and strict about accidental database use.

## Expected starting state

Steps 01 through 09 are complete. The core SDK exposes a hand-maintained
`LyraDB`, `StubLyraDB`, the database error hierarchy, `RunCancelledError`, and
the existing `RunContext` protocol. The worker uses the private
`RunProgressState` helper while retaining its production-only event policy.
There is no SDK-owned concrete run context.

## Public API

Add `LocalRunContext` in `lyra.sdk.local` and export it from the SDK's intended top-level public surface.

It must structurally satisfy `RunContext` without changing `RunContext` from a protocol into a concrete base class.

The constructor must accept:

- `job_id`: a non-empty identifier chosen by the caller;
- `metric`: a non-empty metric-name string;
- `temp_dir`: a caller-owned path for plugin output;
- `db`: an optional `LyraDB`, defaulting to a fresh `StubLyraDB`; and
- `logger`: an optional standard-library logger, with a stable SDK-provided default.

Use the existing `RunContext` property types as the source of truth. Do not create local-only substitutes for SDK models.

## Required behavior

### 1. Temporary-directory handling

The context must ensure `temp_dir` exists, creating its parent directories when necessary. It must fail clearly if the path exists but is not a directory.

The caller owns the directory and its contents. `LocalRunContext` must not automatically delete it, create a hidden temporary directory, or manage a cleanup lifecycle. This keeps plugin output available for inspection after execution.

### 2. Strict database default

If no database is supplied, create a fresh `StubLyraDB` for that context.

The `db` property must never be `None`. Any database call on the default stub must raise `DatabaseNotConfiguredError`, identifying the attempted operation without exposing sensitive arguments.

Allow callers to inject any `LyraDB` implementation, including a small test fake, without requiring Postgres dependencies.

### 3. Event capture

`report_progress()` and `report_message()` must construct the same `JobProgressEvent` and `JobMessageEvent` models used by the production worker.

Use timezone-aware UTC timestamps, as the production event models require.

Store every accepted event in one chronological stream. Expose read-only snapshots with these public properties:

- `events`: a tuple containing all progress and message events in report order;
- `progress_events`: a tuple containing only progress events; and
- `message_events`: a tuple containing only message events.

Do not expose the mutable internal collection.

Use `RunProgressState` for transition validation. Unlike the production worker, the local context must not throttle, coalesce, delay, or persist events. Every accepted call should be immediately visible in the captured event stream.

An invalid progress transition must raise without appending an event or changing the baseline for the next valid transition.

### 4. Cancellation

Provide:

- a `cancel()` method that marks the local run as cancelled; and
- a read-only `cancelled` property.

Before cancellation, `check_cancelled()` must return normally. After cancellation, every call to `check_cancelled()` must raise `RunCancelledError`.

Cancellation is cooperative. `cancel()` must not interrupt the plugin, kill a thread, or mutate captured events.

### 5. Logging

If the caller does not provide a logger, use
`logging.getLogger("lyra.sdk.local")`. Do not add handlers, modify global
logging configuration, or emit setup messages.

## Expected file areas

The implementer should confirm exact paths before editing. The likely areas are:

- a new `packages/lyra_sdk/src/lyra/sdk/local.py`;
- SDK export modules;
- SDK tests; and
- SDK public API documentation or docstrings.

## Tests

Add tests that cover:

- construction with the minimum required arguments;
- rejection of blank `job_id` and `metric` values;
- protocol/type compatibility with `RunContext`;
- creation and preservation of the caller-owned temporary directory;
- failure when `temp_dir` is a file;
- default logger behavior and explicit logger injection;
- default `StubLyraDB` failure for every database operation;
- injection and use of a fake `LyraDB`;
- chronological mixed progress/message capture;
- filtered event snapshots and their immutability;
- production-equivalent event-model validation;
- progress-transition validation and state recovery after a rejected event;
- cooperative cancellation; and
- direct invocation of at least one representative plugin callable or `PluginDefinition` entry point with the local context.

The representative plugin test should prove that no application package, database server, worker, or job store is required.

## Acceptance criteria

- A plugin author can construct `LocalRunContext` from the SDK alone.
- A plugin can read context metadata, write files, log, report progress/messages, and check cancellation.
- Database access fails immediately and clearly unless the caller injects a database.
- Captured events are typed, chronological, complete, and inspectable after the call.
- The SDK's core import path does not import SQLAlchemy, Psycopg, GeoPandas, or application modules.
- All added and affected tests pass.
- Required repository formatting, linting, type-checking, and test checks pass.

## Non-goals

- Connecting to a live Postgres/PostGIS database.
- Owning or cleaning up a temporary directory.
- Reproducing production progress throttling or persistence.
- Validating a plugin's returned metric result.
- Resolving production spatial references or other backend-owned inputs.
- Providing a plugin runner, CLI, or shared execution kernel.
- Adding database schema/version contract checks.

## Verification

Run:

1. `uv run ruff format .`
2. `uv run ruff check .`
3. `uv run ty check`
4. the SDK local-context and related context tests;
5. the representative direct-plugin execution test; and
6. the full test suite with the repository's coverage command if required by the implementation scope.

When running the full suite, generate `coverage.xml`.

Also build or install the core SDK without its optional Postgres dependencies and verify that importing `LocalRunContext` succeeds.

## Implementation record

Complete this section when the step is implemented.

- Date: 2026-07-28
- Material changes: Added and top-level-exported the dependency-light
  `LocalRunContext` with validated metadata, caller-owned directory creation,
  a fresh strict `StubLyraDB` default, optional database and logger injection,
  immediate typed progress/message capture through shared event models and
  `RunProgressState`, immutable tuple snapshots, and cooperative cancellation.
  Added generated API-reference input and focused SDK coverage for construction,
  protocol compatibility, directory ownership, logging, every stub operation,
  fake database use, event validation/order/snapshots, rejected-transition
  recovery, cancellation, and direct `PluginDefinition` execution.
- Verification: `uv run ruff format .`, `uv run ruff check .`, and `uv run ty
  check` passed. Focused local/context/plugin/database coverage passed with `uv
  run pytest tests/test_sdk_local_context.py tests/test_sdk_context.py
  tests/test_sdk_plugin.py tests/test_sdk_database.py -q` (71 tests). `npm run
  generate --prefix docs` regenerated the API reference. `uv build --package
  lyra-sdk` built the source and wheel distributions, and an isolated core-wheel
  environment imported `LocalRunContext` while actively blocking SQLAlchemy,
  Psycopg, GeoPandas, and application imports. The full coverage command `uv run
  pytest --cov=lyra_app --cov=lyra --cov-report=term-missing
  --cov-report=xml` passed (823 tests, 18 live-PostGIS tests skipped because
  `LYRA_TEST_POSTGIS_URL` was not configured, 86% total coverage) and regenerated
  `coverage.xml`.
- Deviations or follow-up notes: No design deviations. The isolated wheel check
  required network access to populate a missing cached core dependency. No
  implementation decision changed a later handoff assumption.
