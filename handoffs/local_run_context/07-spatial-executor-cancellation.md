# Step 07: Correct spatial executor cancellation

Status: `complete`

Depends on: [Step 06: Atomic application database runtime startup](06-atomic-database-runtime-startup.md)

## Objective

Ensure a cancelled asynchronous caller does not release spatial capacity until
the underlying synchronous threaded operation has actually stopped.

## Current state

`ApplicationDatabaseRuntime.run_spatial()` acquires an asyncio semaphore, awaits
`run_in_executor()`, and releases the semaphore in `finally`. Cancelling the
awaiting task can mark the asyncio future cancelled while its thread continues
executing. Capacity is therefore returned early and additional calls can be
queued beyond the intended bound.

PostgreSQL statement timeouts limit database execution duration but do not make
Python threads cancellable and do not prevent an executor queue from growing.

## Required implementation

### Capacity ownership

- One capacity permit represents one submitted executor operation, not one
  awaiting HTTP coroutine.
- Acquire the permit before submitting work.
- Release it exactly once, only after the underlying executor future reaches a
  terminal state.
- On ordinary completion or exception, return/raise normally and release
  capacity.
- On caller cancellation, propagate `CancelledError` promptly while arranging
  for capacity release when the still-running future completes.
- Protect the underlying future from being marked complete merely because its
  waiter was cancelled.
- Handle races between cancellation and completion without double release.

Use event-loop-safe callbacks and retain any references necessary for reliable
completion. Do not block the event loop waiting for a cancelled caller's thread.

### Admission deadline

Preserve the existing timeout while waiting to acquire spatial capacity.
Timeout begins before admission and still raises the existing capacity error.
Once admitted, the database statement timeout governs query duration; do not
apply the pool-admission timeout to the entire operation.

### Shutdown

Runtime shutdown must continue waiting for running executor tasks and cancel
work that has not started where supported. A cancelled caller with live
underlying work must not cause the runtime to report shutdown complete early.

## Deterministic tests

Use thread synchronization primitives to create controlled operations:

- start one operation and block it inside the executor;
- cancel its awaiting task;
- prove a second operation cannot enter while the first thread remains blocked;
- unblock the first operation and prove capacity becomes available exactly
  once;
- cover cancellation immediately before and after completion;
- cover worker exception and normal result paths;
- cover capacity-acquisition timeout;
- cover runtime close while cancelled-caller work is still active.

Avoid sleep-based assertions except for short bounded “must not complete”
guards.

## Expected files

Likely areas include:

- `lyra_app/db/connection.py`;
- `tests/test_database_runtime.py`;
- possibly lifespan tests if shutdown behavior changes observably.

## Acceptance criteria

- Semaphore capacity matches actually running or queued executor operations.
- Caller cancellation remains prompt.
- Running thread work is never treated as cancelled when it is not.
- Capacity is released once on every path.
- Existing admission timeout and successful spatial behavior remain intact.

## Verification

Run the required Python checks, the deterministic cancellation/runtime tests,
job-submission spatial-resolution tests, and the full suite with coverage.

## Non-goals

- Do not attempt to kill Python threads.
- Do not introduce database query cancellation APIs.
- Do not change statement-timeout values.
- Do not add retries, rate limits, or local runtime behavior.

## Implementation record

Complete this section when the step is implemented.

- Date: 2026-07-28
- Material changes: Transferred each admitted spatial permit to the submitted
  executor future, protected that future from caller cancellation with
  `asyncio.shield()`, and released capacity from an event-loop completion
  callback exactly once after underlying work terminates. Preserved immediate
  release when executor submission itself fails and retained the existing
  admission-only timeout. Added deterministic thread-synchronization tests for
  cancelled callers retaining capacity, cancellation immediately before and
  after worker completion, normal results, worker exceptions, submission
  failures, admission timeout, exact release counts, and runtime shutdown
  waiting for live work whose caller was cancelled.
- Verification: `uv run ruff format .`, `uv run ruff check .`, and `uv run ty
  check` passed; `uv run pytest tests/test_database_runtime.py -q` passed (21
  tests); job-submission spatial-resolution coverage passed with `uv run pytest
  tests/test_jobs_route.py -k 'spatial or cvegeo or met_zone' -q` (5 tests, 79
  deselected); and `uv run pytest --cov=lyra_app --cov=lyra
  --cov-report=term-missing --cov-report=xml` passed (764 tests, 13 live-PostGIS
  tests skipped because `LYRA_TEST_POSTGIS_URL` was not configured, 86% total
  coverage) and regenerated `coverage.xml`.
- Deviations or follow-up notes: None. Shutdown continues to use executor
  shutdown with `wait=True` and `cancel_futures=True`; no later handoff
  assumption changed.
