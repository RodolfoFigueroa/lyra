# Inspect Cache

## Goal

Avoid repeating expensive Celery worker inspection for admin routes that are
called close together.

## Background From The Discussion

`/admin/workers` and `/admin/queues` both call `inspect_workers()`. A client that
fetches both routes pays the inspect cost twice. After the inspect timeout step,
each single inspect should be faster, but repeated route calls can still waste
time and broker traffic.

## Scope

- Add a short in-process TTL cache for `WorkerInspectSnapshot`.
- Make `list_workers()`, `get_worker()`, and `list_queues()` use the cached
  snapshot path.
- Provide a bypass for operations that must force a fresh inspect if needed.
- Add deterministic tests for cache hit, cache expiry, and optional forced
  refresh.

## Out Of Scope

- No background polling yet.
- No cross-process cache.
- No persistent Redis-backed status cache.
- No TUI changes.
- No change to job or plugin routes.

## Files Or Areas Likely Affected

- `lyra_app/worker_control.py`
- `lyra_app/routes/admin.py`
- `tests/test_worker_control.py`
- `tests/test_observability_routes.py`

## Required Behavior

- Repeated worker/queue route calls within the TTL should share the same
  `WorkerInspectSnapshot`.
- Cache expiry should trigger a fresh Celery inspect.
- Tests should not depend on wall-clock sleeps where a fake clock can be used.
- Errors or missing Celery data should still be cached briefly to avoid hammering
  remote control during outages.
- Existing response models should remain compatible.

## Implementation Notes

- Keep the cache in `lyra_app.worker_control`, next to `inspect_workers()`.
- Prefer a small function surface such as:

  ```python
  def get_worker_inspect_snapshot(*, force_refresh: bool = False) -> WorkerInspectSnapshot:
      ...
  ```

- Keep `inspect_workers()` as the low-level live probe so tests and exceptional
  paths can still call it directly.
- Use `time.monotonic()` for TTL age.
- Consider cache state shaped like:

  ```python
  _CACHED_SNAPSHOT: WorkerInspectSnapshot | None
  _CACHED_AT: float | None
  WORKER_INSPECT_CACHE_TTL_SECONDS = 1.0
  ```

- Update admin routes to call `get_worker_inspect_snapshot()` instead of
  `inspect_workers()` directly.
- Tests can monkeypatch the clock and low-level inspect function.
- Keep the cache small and transparent; this is the conservative alternative to
  a background poller.

## Tests And Verification

- Add worker-control tests proving:
  - first call inspects live
  - second call inside TTL reuses the snapshot
  - call after TTL inspects live again
  - `force_refresh=True` bypasses the cache
- Update route tests to monkeypatch the cached snapshot provider instead of the
  low-level inspect function where appropriate.
- Run:

  ```bash
  uv run pytest tests/test_worker_control.py tests/test_observability_routes.py
  uv run ruff format
  uv run ruff check --fix
  uv run ty check --fix
  ```

## Completion Criteria

- Paired `/admin/workers` and `/admin/queues` calls in the same API process do
  not run two live Celery inspect passes within the TTL.
- Existing route behavior and response models remain compatible.
- Focused tests pass without timing flakiness.

## Handoff Notes For The Next Step

Proceed to worker identity. The cache improves speed, but the current response
can still report configured workers as offline while observed Celery workers
appear under default `celery@<container-id>` names.

