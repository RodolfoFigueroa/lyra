# Step 06: Atomic application database runtime startup

Status: `planned`

Depends on: [Step 05: Read-only connection profiles](05-read-only-connection-profiles.md)

## Objective

Make `ApplicationDatabaseRuntime` initialization all-or-nothing so failed
startup cannot leak resources or leave an object that appears started while
missing an engine, executor, or capacity guard.

## Current state

`ApplicationDatabaseRuntime.start()` assigns the async engine to instance state
before constructing the spatial engine, thread executor, and semaphore. Its
idempotence check only examines the async engine. The application lifespan calls
`start()` before entering the `try/finally` that later closes the runtime.

Engine construction is usually lazy, but resource creation can still fail.
Future connection-profile changes also increase the amount of setup performed
during startup.

## Required implementation

### Lifecycle invariants

Define and enforce exactly two externally visible states:

- stopped: no published engines, executor, or semaphore;
- started: all required resources exist and are mutually consistent.

Do not represent a partial state through a subset of non-null attributes.
`start()` and `close()` must remain safe when called in normal FastAPI lifespan
order.

### Atomic start

- Construct new engines, executor, and semaphore into local variables.
- Publish them to instance state only after all construction succeeds.
- If any construction step fails, dispose or shut down every resource already
  created, leave the runtime stopped, and re-raise the original exception.
- A second `start()` on an already fully started runtime remains a no-op.
- A `start()` following a failed attempt performs a complete fresh attempt.
- Prevent two concurrent `start()` calls from publishing competing resources.
  A simple lifecycle lock is acceptable; do not build a general state machine.

### Reliable close

- `close()` remains idempotent.
- Clear published state exactly once and dispose each captured resource.
- Attempt all cleanup operations even if one disposal fails; preserve useful
  exception context according to existing project conventions.
- Do not close an engine still owned by another runtime.
- Preserve graceful waiting for already running executor work. Cancellation
  semantics are handled separately in Step 07.

### Lifespan integration

Ensure the FastAPI lifespan cannot skip cleanup of resources created during a
failed start. This may be satisfied by atomic self-cleanup, lifespan
restructuring, or both. Tests must cover the actual lifespan path, not only the
runtime class.

## Tests

Use controlled fake resources to fail each construction stage and assert:

- previously created resources are disposed;
- no instance attribute advertises a partial start;
- retry succeeds;
- repeated start does not duplicate resources;
- repeated close is harmless;
- lifespan startup failure performs cleanup;
- successful start/close behavior and shutdown ordering remain intact.

No test should depend on real time or a live database.

## Expected files

Likely areas include:

- `lyra_app/db/connection.py`;
- `lyra_app/main.py`;
- `tests/test_database_runtime.py`;
- `tests/test_main_lifespan.py`.

## Acceptance criteria

- Partial initialization is impossible to observe after `start()` returns or
  raises.
- All created resources are cleaned after every injected startup failure.
- Retrying after failure works.
- Existing successful application startup and shutdown behavior is unchanged.
- No query, adapter, or public SDK behavior changes.

## Verification

Run the required Python checks, plus focused database-runtime and lifespan
tests. Run the full suite with coverage because the application lifecycle is a
global boundary.

## Non-goals

- Do not change spatial task cancellation or semaphore behavior.
- Do not add database probes or contract checks.
- Do not change error classification.
- Do not add local runtime features.

## Implementation record

Complete this section when the step is implemented.

- Date:
- Material changes:
- Verification:
- Deviations or follow-up notes:
