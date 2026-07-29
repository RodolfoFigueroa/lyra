# Local run context and database hardening implementation series

## Purpose

This directory is the implementation source of truth for adding a supported
`LocalRunContext` and optional live PostgreSQL/PostGIS access to `lyra-sdk`.
The work also hardens the existing database boundary before it is reused outside
the Lyra application.

Each numbered document defines one independently mergeable implementation step.
A fresh Codex session should implement exactly one document at a time, in
numeric order. Every step must leave the repository formatted, type-safe, and
testable; no step may rely on deliberately broken intermediate behavior.

## How to use this series

Before implementing a step:

1. Read this index and the complete document for that step.
2. Confirm that every listed dependency is already implemented in the working
   tree. Earlier handoffs describe intended end state, but the current code and
   completed implementation records establish what is actually present.
3. Inspect the named current-state files again because line numbers and internal
   structure may have changed during earlier steps.
4. Keep the step's non-goals out of scope, even when an adjacent improvement
   appears convenient.

After implementing a step:

1. Run every verification command required by the repository `AGENTS.md`.
2. Change the document status from `planned` to `complete`.
3. Fill in its implementation record with the date, files materially changed,
   verification results, and any deliberate deviation from the document.
4. If a deviation changes a later step's assumptions, update the affected
   handoff documents in the same change.

The documents prescribe behavior and boundaries, not exact line-by-line code.
When multiple implementations satisfy the contract, prefer the smallest design
consistent with existing project conventions.

## Sequence

| Step | Document                                                                               | Outcome                                                                                      |
| ---- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 01   | [PostGIS integration harness](01-postgis-integration-harness.md)                       | Real database behavior is covered in CI before refactoring.                                  |
| 02   | [SDK database interface](02-sdk-database-interface.md)                                 | `LyraDB` becomes a hand-maintained SDK contract and gains a strict stub.                     |
| 03   | [PostgreSQL adapter extraction](03-postgres-adapter-extraction.md)                     | The worker and future local runtime share one optional SDK adapter.                          |
| 04   | [Spatial query hardening](04-spatial-query-hardening.md)                               | Identifiers, runtime arguments, schema selection, and ordering are safe and deterministic.   |
| 05   | [Read-only connection profiles](05-read-only-connection-profiles.md)                   | API, worker, integration, and local connections have explicit read-only workload policy.     |
| 06   | [Atomic database runtime startup](06-atomic-database-runtime-startup.md)               | API database resources cannot be left partially initialized.                                 |
| 07   | [Spatial executor cancellation](07-spatial-executor-cancellation.md)                   | Cancelled callers do not release capacity while threaded work is still running.              |
| 08   | [Database error taxonomy](08-database-error-taxonomy.md)                               | Only genuinely transient failures are reported as retryable outages.                         |
| 09   | [Shared run-context event semantics](09-run-context-event-semantics.md)                | Worker and local contexts share progress validation and cancellation semantics.              |
| 10   | [LocalRunContext core](10-local-run-context.md)                                        | Plugins can run with local logging, files, events, cancellation, and a strict database stub. |
| 11   | [Local PostgreSQL connectivity](11-local-postgres-connectivity.md)                     | A managed local context can query the central PostGIS instance.                              |
| 12   | [Documentation and release verification](12-documentation-and-release-verification.md) | Public guidance, packaging, generated references, and end-to-end checks are complete.        |

Dependencies are intentionally linear even where implementation could happen in
parallel. Sequential execution reduces migration states and gives each fresh
session one known starting point.

## Cross-cutting invariants

Every implementation step must preserve these decisions:

- `RunContext` remains the plugin-facing structural protocol.
- The worker remains the owner of production persistence, Redis event delivery,
  rate limiting, and cooperative cancellation state.
- `LyraDB` remains a non-null, read-only, high-level API. Missing local database
  configuration is represented by a strict object that raises on use, not by
  `None`.
- SQLAlchemy engines are owned by runtimes or context managers. Database
  adapters use engines but never dispose engines they did not create.
- `lyra-sdk` remains lightweight by default. PostgreSQL, SQLAlchemy, Psycopg,
  and GeoPandas support is optional and must not be imported by the core SDK
  import path.
- Database values use bound parameters. Dynamic identifiers use a
  dialect-aware composition mechanism and are never interpolated as raw caller
  text.
- Each high-level `LyraDB` method owns its connection and read transaction.
  Multiple method calls do not promise a common snapshot.
- Local messages and progress are captured without production rate limiting or
  coalescing.
- The application and the local runtime use the same concrete PostgreSQL
  implementation.
- Secrets and password-bearing URLs must not appear in representations, logs,
  assertion messages, or user-facing errors.

## Explicitly deferred work

The following work is not part of this series:

- database schema-version records, compatibility negotiation, versioned views,
  migration orchestration, or readiness checks for required tables and
  extensions;
- a shared worker-equivalent metric execution kernel;
- local result normalization or worker-equivalent output validation;
- a `lyra-plugin run` CLI;
- public spatial-reference resolution for local runs;
- database request rate limiting, per-author authorization, or credential
  issuance automation;
- cross-method snapshot or unit-of-work APIs;
- general database write support;
- automatic retry of plugin database calls;
- result streaming, row limits, or a redesign of the `LyraDB` data-return
  contract.

The existing `SELECT 1` connectivity probes may remain. Step 01 adds integration
coverage, but it must not turn those probes into a database contract check.

## Verification baseline

All Python-changing steps must use `uv` and follow `AGENTS.md`. At minimum:

1. `uv run ruff format .`
2. `uv run ruff check .`
3. `uv run ty check`
4. tests relevant to the changed code

When a step runs the full suite, it must generate `coverage.xml` with the
project's CI command:

`uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml`

Steps that change packaging must also build the affected distributions and
smoke-test imports in an isolated environment.
