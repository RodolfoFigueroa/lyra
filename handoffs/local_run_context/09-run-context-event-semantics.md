# Step 09: Share Run-Context Event Semantics

Status: `planned`

Depends on:

- [Step 08: Define a Stable Database Error Taxonomy](08-database-error-taxonomy.md)

## Objective

Move the backend-neutral rules for progress reporting and cancellation into the SDK so both the production worker context and the future local context use the same semantics.

This step must preserve the production worker's observable event behavior. It does not add `LocalRunContext` yet.

## Expected starting state

Steps 01 through 08 are complete. `RunContext` is still defined in
`packages/lyra_sdk/src/lyra/sdk/context.py`, while `WorkerRunContext` in
`lyra_app/worker.py` still implements progress-transition state, production
event policy, and cancellation polling. `job_store.JobCancelledError` remains
the application-specific cooperative-cancellation exception.

## Why this step exists

`WorkerRunContext` currently owns two kinds of behavior:

1. backend-neutral semantics, such as validating progress transitions; and
2. production concerns, such as rate limiting, coalescing, persistence, and polling the job store for cancellation.

The local context needs the first category but must not inherit the second. Extracting only the common semantics prevents the two contexts from drifting while keeping production-specific policy in the application.

## Required changes

### 1. Add a public cancellation exception to the SDK

Define `RunCancelledError` in the SDK's run-context API and export it from the same public surfaces as `RunContext`.

`RunContext.check_cancelled()` should document that it raises this exception when cancellation has been requested. Existing plugins that never catch the exception must continue to work unchanged.

Keep application compatibility by making the existing `job_store.JobCancelledError` a subclass of `RunCancelledError`, or by replacing it in a way that preserves any existing application imports and exception handlers. Production cancellation must still be distinguishable where the application currently relies on `JobCancelledError`.

### 2. Extract progress-transition validation

Introduce an SDK-owned helper named `RunProgressState`. Place it in a private
SDK module and do not export it from the top-level plugin API. It is a shared
implementation utility, not a new plugin-facing service.

The helper must:

- accept validated `JobProgressEvent` values;
- retain the last accepted progress event;
- reject a decrease in `current` while the stage remains unchanged;
- allow `total` to change from unknown to a concrete value within a stage, but
  reject any later change or removal once it is concrete;
- require `unit`, including an initial `None`, to remain unchanged within a
  stage;
- allow a new stage to establish a new `current`, `total`, and `unit`;
- update its state only after an event passes validation; and
- raise `ValueError` for invalid transitions, matching the existing worker-facing failure style.

Field-level validation, including valid values and relationships within one event, remains the responsibility of `JobProgressEvent`. Do not duplicate its Pydantic validation in `RunProgressState`.

The accepted transition rules must match the behavior of the current `WorkerRunContext`. If characterization tests reveal an edge case not described above, preserve that behavior and record it in this document's implementation record.

### 3. Use the shared helper in `WorkerRunContext`

Replace the worker's private progress-transition state with `RunProgressState`.

The following worker behavior must remain application-owned and unchanged:

- progress throttling;
- progress coalescing;
- event persistence;
- event ordering;
- worker logging;
- cancellation polling; and
- job-store-specific exception behavior.

The shared helper should run at the same logical point as the existing validation. An invalid event must not change the worker's last valid progress state or enter its persistence/coalescing path.

### 4. Keep message semantics model-driven

Continue using `JobMessageEvent` as the shared validation contract for messages. No separate message-state helper is needed because messages have no cross-event transition rules.

## Expected file areas

The implementer should confirm exact paths before editing. The likely areas are:

- `packages/lyra_sdk/src/lyra/sdk/context.py`;
- the SDK package export modules;
- `lyra_app/worker.py`;
- `lyra_app/job_store.py`;
- SDK tests for context semantics; and
- worker and job-store tests.

## Tests

Add focused tests that prove:

- `RunProgressState` accepts the first event;
- `RunProgressState` accepts monotonic progress within a stage;
- an initially unknown `total` may become concrete and is then stable;
- a new stage resets the transition baseline;
- decreasing `current` within a stage is rejected;
- changing or removing a concrete `total` is rejected;
- changing `unit`, including adding one after an initial `None`, is rejected;
- a rejected event does not poison the state for a later valid event;
- `JobCancelledError` is catchable as `RunCancelledError`;
- worker cancellation still follows its existing job-store behavior; and
- the worker emits, coalesces, and persists the same event sequence as before this refactor.

Prefer characterization tests around the worker's existing throttling and coalescing behavior instead of rewriting those policies during this step.

## Acceptance criteria

- `RunCancelledError` is a documented public SDK exception.
- `RunContext.check_cancelled()` has one backend-neutral cancellation contract.
- `RunProgressState` is the only progress-transition implementation used by the worker.
- Production throttling, coalescing, persistence, and cancellation behavior are unchanged.
- All added and affected tests pass.
- Required repository formatting, linting, type-checking, and test checks pass.

## Non-goals

- Adding `LocalRunContext`.
- Changing worker event rates or coalescing policy.
- Changing job-event persistence schemas.
- Adding a shared plugin execution kernel or CLI.
- Adding database schema/version contract checks.

## Verification

Run:

1. `uv run ruff format .`
2. `uv run ruff check .`
3. `uv run ty check`
4. the SDK context tests;
5. the worker and job-store tests affected by the refactor; and
6. the full test suite with the repository's coverage command if required by the implementation scope.

When running the full suite, generate `coverage.xml`.

## Implementation record

Complete this section when the step is implemented.

- Date:
- Material changes:
- Verification:
- Deviations or follow-up notes:
