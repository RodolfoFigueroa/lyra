# Background Snapshot

## Goal

Provide a non-blocking worker-status source for admin routes if the conservative
timeout, cache, and worker-name fixes are still insufficient.

## Background From The Discussion

A background status collector is a common pattern for slow or failure-prone
observability dependencies, but it is more complex than a TTL cache. The agreed
approach is to plan it, but gate implementation until simpler fixes are measured.

## Scope

- Add an API-owned background worker-inspect collector only if validation after
  steps 1-3 still shows unacceptable latency.
- Serve `/admin/workers`, `/admin/workers/{worker_name}`, and `/admin/queues`
  from the last collected snapshot.
- Expose enough metadata to make stale data understandable.
- Keep failures contained so collector errors do not crash the API.

## Out Of Scope

- Do not implement this step before the validation gate is met.
- No distributed cross-process collector.
- No Redis-backed observability cache unless explicitly chosen later.
- No TUI changes.
- No replacement of Celery worker execution.

## Files Or Areas Likely Affected

- `lyra_app/worker_control.py`
- `lyra_app/main.py`, or the existing FastAPI lifespan/startup area
- `lyra_app/routes/admin.py`
- `packages/lyra_sdk/src/lyra/sdk/models/observability.py`, if response metadata
  is added
- `tests/test_worker_control.py`
- `tests/test_observability_routes.py`
- possibly `tests/test_api_client_jobs.py`, if SDK response models change
- `docs/src/content/docs/operations.md`

## Required Behavior

- Admin routes should return quickly without performing live Celery inspect in
  the request path.
- The collector should refresh snapshots on a short interval and store the last
  successful or last attempted observation.
- Route responses should make stale or unavailable inspect data clear.
- The API should start and stop the collector cleanly.
- If no snapshot has been collected yet, routes should degrade to unknown state
  quickly instead of blocking for a full live inspect.

## Implementation Notes

- Prefer FastAPI lifespan integration over ad hoc global task startup.
- Keep collector state in one small module with explicit start/stop functions.
- Track metadata such as:
  - `observed_at`
  - `age_seconds`
  - `stale`
  - `last_error`, if safe to expose
- Make interval and stale threshold conservative constants first unless a config
  location is already available.
- Decide whether routes should perform one best-effort live inspect on cold
  startup or immediately return unknown. The conservative default is immediate
  unknown with metadata.
- Be explicit that multi-process API deployments will run one collector per
  process.

## Tests And Verification

- Add tests for collector start, snapshot update, stale marking, and stop.
- Add route tests proving worker and queue routes do not call live inspect in the
  request path when a background snapshot exists.
- Add cold-start route tests for no snapshot yet.
- If response models gain metadata fields, update SDK/client tests.
- Run:

  ```bash
  uv run pytest tests/test_worker_control.py tests/test_observability_routes.py tests/test_api_client_jobs.py
  uv run ruff format
  uv run ruff check --fix
  uv run ty check --fix
  ```

## Completion Criteria

- This step was implemented only after steps 1-3 were measured and judged
  insufficient.
- Worker and queue routes no longer block on live Celery inspect during normal
  requests.
- Responses expose stale/unknown state clearly.
- Collector lifecycle is covered by tests.

## Handoff Notes For The Next Step

Proceed to final validation. Pay special attention to route latency, stale-state
behavior, and service cleanup.

