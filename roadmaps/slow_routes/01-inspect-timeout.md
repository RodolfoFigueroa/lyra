# Inspect Timeout

## Goal

Make Celery worker inspection fail fast enough for admin observability routes to
remain responsive when workers are offline, slow, or not replying to remote
control.

## Background From The Discussion

`/admin/workers` and `/admin/queues` each took about five seconds. The current
`inspect_workers()` function calls Celery inspect methods `active`, `reserved`,
`scheduled`, `stats`, and `active_queues`. Celery's inspect object defaults to a
`1.0s` timeout, so five inspect calls can make one route take about five seconds.

## Scope

- Add an explicit short timeout to `celery_app.control.inspect(...)`.
- Keep the timeout close to `inspect_workers()` so the behavior is obvious.
- Add tests that verify the timeout is passed into Celery inspect.
- Preserve graceful degraded responses when inspect data is missing.

## Out Of Scope

- No caching in this step.
- No background worker-status collector in this step.
- No TUI changes.
- No route schema changes unless they are already required by existing tests.

## Files Or Areas Likely Affected

- `lyra_app/worker_control.py`
- `tests/test_worker_control.py`
- possibly `docs/src/content/docs/operations.md`, if operator-facing behavior is
  documented in this step

## Required Behavior

- `inspect_workers()` should use an explicit timeout shorter than Celery's
  default.
- If workers do not respond before the timeout, the route should still return an
  unknown/offline-compatible snapshot rather than raising.
- Existing route callers should not need to pass timeout arguments.
- The literal `/admin/status` route should remain independent from worker
  inspection.

## Implementation Notes

- Prefer a small constant first, for example
  `DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS = 0.5`, unless there is already an
  established config location for observability timings.
- If making it configurable is low-risk, add a config field with a conservative
  default and tests for the default. Avoid large config reshaping in this step.
- Pass the timeout when constructing the inspector:

  ```python
  inspector = celery_app.control.inspect(timeout=DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS)
  ```

- Keep `_inspect_call()` defensive. Timeout behavior should produce `None`
  sections and `inspect_available=False` when no data arrives.
- Do not optimize away any of the five inspect calls yet; that belongs to later
  steps.

## Tests And Verification

- Update fake Celery control objects in `tests/test_worker_control.py` to record
  `inspect()` keyword arguments.
- Add a unit test asserting that `inspect_workers()` passes the chosen timeout.
- Keep existing tests for normal data and unknown inspect state passing.
- Run:

  ```bash
  uv run pytest tests/test_worker_control.py tests/test_observability_routes.py
  uv run ruff format
  uv run ruff check --fix
  uv run ty check --fix
  ```

## Completion Criteria

- Worker inspect uses an explicit short timeout.
- Unknown inspect state is still represented without exceptions.
- Focused tests pass.
- No API route schema churn was introduced by this step.

## Handoff Notes For The Next Step

Proceed to the TTL cache step. The next step should reduce duplicate inspect
work when `/admin/workers` and `/admin/queues` are requested close together.

