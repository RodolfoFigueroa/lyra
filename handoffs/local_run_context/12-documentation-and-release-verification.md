# Step 12: Document and Release-Verify the Local Plugin Workflow

Status: `planned`

Depends on:

- [Step 01: Add a Real PostGIS Integration Harness](01-postgis-integration-harness.md)
- [Step 02: Establish the SDK Database Interface and Stub](02-sdk-database-interface.md)
- [Step 03: Extract the Postgres Adapter into the SDK](03-postgres-adapter-extraction.md)
- [Step 04: Harden Spatial Query Construction](04-spatial-query-hardening.md)
- [Step 05: Add Explicit Read-Only Connection Profiles](05-read-only-connection-profiles.md)
- [Step 06: Make Database Runtime Startup Atomic](06-atomic-database-runtime-startup.md)
- [Step 07: Make Spatial Executor Cancellation Capacity-Safe](07-spatial-executor-cancellation.md)
- [Step 08: Define a Stable Database Error Taxonomy](08-database-error-taxonomy.md)
- [Step 09: Share Run-Context Event Semantics](09-run-context-event-semantics.md)
- [Step 10: Add `LocalRunContext`](10-local-run-context.md)
- [Step 11: Connect `LocalRunContext` to Postgres/PostGIS](11-local-postgres-connectivity.md)

## Objective

Finish the feature series by documenting the supported plugin-development workflows, verifying the published package shapes, and removing stale documentation left by the database refactor.

This step is documentation and release hardening. It must not introduce another execution abstraction or broaden the database contract.

## Expected starting state

Steps 01 through 11 are complete and their implementation records describe any
approved deviations. The SDK supports core-only local execution, fake database
injection, and managed live Postgres/PostGIS connectivity. The application and
local path use the same `PostgresLyraDB` implementation.

## Required documentation

### 1. Explain the three local database modes

Document these distinct workflows for plugin authors:

1. **No database**: construct `LocalRunContext` with its default `StubLyraDB`. Database calls fail immediately with `DatabaseNotConfiguredError`.
2. **Test fake**: inject a plugin- or test-owned `LyraDB` fake for deterministic unit tests.
3. **Live Postgres/PostGIS**: use `LocalRunContext.connect_postgres` inside its context manager with the optional Postgres dependencies installed.

For each mode, explain:

- when to choose it;
- who owns resources;
- what the plugin can inspect after execution;
- what failures to expect; and
- whether any external service is required.

Include concise, runnable examples using public imports. Keep them focused on direct plugin invocation rather than inventing a local runner API.

### 2. Document SDK installation boundaries

Document:

- installation of the core SDK for stub/fake use;
- installation of the Postgres optional extra for live database use;
- the Python and dependency constraints actually declared by the package;
- the error users see when they request live connectivity without the extra; and
- confirmation that application-only modules are not public SDK dependencies.

Use the repository's actual package names and supported `uv` commands.

### 3. Document live-database operations and risk

Document the first-iteration trust model accurately: plugin authors are vetted and receive direct database connectivity.

Operational guidance must cover:

- using a dedicated login role rather than the application's owner/migration role;
- granting only connect, schema usage, required table/view reads, and required function execution;
- setting read-only transaction defaults on the role or connection profile;
- configuring the allowed schema explicitly;
- protecting and rotating the database URL;
- avoiding credentials in source control, command history, logs, screenshots, and issue reports;
- recognizing the local SDK `application_name`;
- revoking access when no longer needed; and
- treating the shared database as real data even though the author workflow is local.

Do not claim that trusted authors, read-only session settings, or `LocalRunContext` form a security boundary. Database permissions remain authoritative.

### 4. Document semantic limitations

State clearly that:

- `LocalRunContext` captures every event and does not reproduce production throttling, coalescing, or persistence;
- cancellation is cooperative;
- the caller owns and retains `temp_dir`;
- direct invocation does not automatically perform production result validation;
- backend-resolved spatial references and other production inputs must be supplied by the test or author;
- each `LyraDB` method has its own read transaction, so multiple calls do not share one snapshot;
- database writes are unsupported;
- automatic retries, streaming, and result-size controls are not part of this iteration; and
- the connector performs only an eager connectivity probe, not a schema/version compatibility check.

### 5. Update API and architecture references

Update public API references for:

- `RunContext`;
- `RunCancelledError`;
- `LocalRunContext`;
- `LyraDB`;
- `StubLyraDB`;
- the database error hierarchy;
- `PostgresLyraDB`; and
- the Postgres connector.

Remove or revise references that say the database client is application-owned or generated into the SDK. Update architecture diagrams or package-boundary descriptions if they would otherwise be inaccurate.

Do not expose implementation-only helpers such as `RunProgressState` as a primary plugin feature.

## Release verification

### 1. Package isolation

Build and inspect the SDK distribution and application distribution independently.

Verify:

- the core SDK installs and imports without Postgres dependencies;
- `LocalRunContext` with the stub and a fake works in the core-only environment;
- the SDK Postgres extra installs all requirements needed by `PostgresLyraDB`;
- live connector imports succeed with that extra;
- the application installs against the packaged SDK rather than relying on source-tree leakage;
- no generated `LyraDB` artifact or obsolete generation hook remains; and
- package metadata includes all new public modules and intended type information.

Use isolated temporary environments or the repository's existing smoke-test approach. Do not mutate the developer's global Python installation.

### 2. Documentation build and link checks

Run the repository's documentation generation/build command and any configured link or reference checks.

Confirm that:

- public imports in examples are correct;
- optional-extra installation commands match package metadata;
- API reference targets resolve;
- no stale links point at the removed application database client; and
- secrets in examples are unmistakably placeholders.

### 3. End-to-end author smoke tests

Run and, where practical, automate two smoke tests using only public APIs:

1. a core-SDK plugin execution with the default stub or a fake, including event capture and a file written to `temp_dir`; and
2. an SDK-with-Postgres-extra plugin execution against the integration database, including at least one real spatial read.

The live smoke test must use a disposable test database or the marked integration environment, never an unspecified shared or production database.

## Expected file areas

The implementer should inspect the repository and update the actual documentation system. Likely areas include:

- plugin-author documentation;
- SDK API/reference documentation;
- database deployment or operations documentation;
- package README files;
- package metadata and smoke-test scripts;
- generated documentation configuration; and
- tests that validate installed distributions.

Avoid changing production behavior in this step. If release verification reveals a defect, fix only a small, directly related packaging or documentation issue. Record larger defects as follow-up work instead of expanding this handoff without bound.

## Acceptance criteria

- A plugin author can follow one documented path from installation through direct plugin execution.
- Stub, fake, and live-database modes are clearly distinguished.
- Resource ownership, event differences, transaction behavior, and security limitations are explicit.
- All examples use supported public APIs and package names.
- Core-only and Postgres-extra SDK installations are independently verified.
- Application package installation is verified against the packaged SDK.
- Documentation builds without stale database-client references.
- Both author smoke tests pass.
- Required repository formatting, linting, type-checking, test, packaging, and documentation checks pass.

## Non-goals

- Implementing database schema/version contract checks.
- Adding a local execution kernel, CLI, or production result validator.
- Adding a database proxy, rate limits, or an authentication service.
- Extending the database API with writes, retries, snapshots, streaming, or row limits.
- Redesigning unrelated documentation or packaging.

## Verification

At minimum, run:

1. `uv run ruff format .`
2. `uv run ruff check .`
3. `uv run ty check`
4. relevant unit and PostGIS integration tests;
5. installed-distribution smoke tests for core SDK, SDK with Postgres extra, and the application;
6. the documentation build and configured reference/link checks;
7. package builds; and
8. the full test suite:

   `uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml`

The full suite must leave a current `coverage.xml`.

Use the repository's more specific documented commands where they differ from the generic names above, and record every command and result.

## Implementation record

Complete this section when the step is implemented.

- Date:
- Material changes:
- Verification:
- Deviations or follow-up notes:
