# 04 Job Operations

## Goal

Add admin job listing and cancellation routes so the TUI can show and control
jobs without knowing job IDs in advance.

## Background From The Discussion

Current job routes can create a job, fetch a known job by ID, stream known job
events, and fetch a known result. They cannot list jobs. Cancellation exists as
an internal worker/job-store concept, but there is no public route to request it.

## Scope

- Add a Redis-backed job index suitable for recent job listing.
- Add `GET /admin/jobs`.
- Add `POST /admin/jobs/{job_id}/cancel`.
- Add typed SDK response models if useful.
- Update route, job-store, worker, client, and docs tests.

## Out Of Scope

- Do not add public unauthenticated job listing.
- Do not add bulk cancellation unless it falls out naturally and is explicitly
  tested.
- Do not build worker/queue dashboards yet.

## Files Or Areas Likely Affected

- `lyra_app/job_store.py`
- `lyra_app/routes/jobs.py`
- `lyra_app/routes/admin.py` or a new admin job route module
- `lyra_app/worker.py`
- `lyra_app/worker_control.py` if Celery revoke helpers are extracted
- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- `packages/lyra_sdk/src/lyra/sdk/models/__init__.py`
- `packages/lyra_api/src/lyra/api/client/sync.py`
- `packages/lyra_api/src/lyra/api/client/async_.py`
- `tests/test_job_store.py`
- `tests/test_jobs_route.py`
- `tests/test_update_plugins.py` or a new `tests/test_admin_jobs.py`
- `tests/test_api_client_jobs.py`
- `docs/src/content/docs/job-api.md`
- `docs/src/content/docs/operations.md`
- `docs/src/content/docs/reference.md`
- `docs/public/llms.txt`

## Required Behavior

### `GET /admin/jobs`

- Requires admin bearer auth.
- Returns recent job status snapshots.
- Supports at least:
  - `limit`
  - optional `status`
  - optional `metric`
- Results should be ordered newest-first by update or creation time.
- Expired jobs should not appear in normal responses.
- The route should degrade predictably if Redis is unavailable.

### `POST /admin/jobs/{job_id}/cancel`

- Requires admin bearer auth.
- If job is queued, started, or progress:
  - Marks status as `cancelled`.
  - Emits a cancellation event.
  - Attempts to revoke the Celery task by job ID.
  - Returns a typed response showing cancellation was requested.
- If job is already terminal:
  - Does not overwrite the terminal result.
  - Returns a clear response or conflict error. Choose one behavior and test it.
- If job is unknown or expired:
  - Returns `404`.

## Implementation Notes

- Job listing probably needs an index such as a Redis sorted set updated by
  `create_job()` and `set_job_status()`.
- Ensure index entries expire or are pruned so the list does not grow forever.
- Avoid scanning all Redis keys in request handlers.
- Think carefully about cancellation race conditions:
  - A job may finish while cancellation is requested.
  - A worker may see cancelled status and persist a `CancelledJobResult`.
  - Do not replace an existing succeeded/failed/cancelled result with a new
    cancellation result after the fact.
- Consider adding job-store helpers such as:
  - `list_job_statuses(...)`
  - `cancel_job(...)`
  - `is_terminal_status(...)`

## Tests And Verification

- Add job-store tests for indexing, filtering, ordering, TTL/pruning behavior,
  and cancellation.
- Add route tests for:
  - list jobs empty
  - list jobs by status
  - list jobs by metric
  - limit validation
  - cancel active job
  - cancel terminal job
  - cancel unknown job
  - Redis failure where appropriate
- Add worker tests if cancellation semantics change.
- Run:

  ```bash
  uv run pytest tests/test_job_store.py tests/test_jobs_route.py tests/test_runner.py
  uv run pytest tests/test_update_plugins.py tests/test_api_client_jobs.py
  uv run ruff format <touched-files>
  uv run ruff check <touched-files>
  ```

## Completion Criteria

- The TUI can populate a job table through `GET /admin/jobs`.
- The TUI can request cancellation through `POST /admin/jobs/{job_id}/cancel`.
- Cancellation does not corrupt already terminal jobs.
- Job listing does not rely on Redis key scans.
- Docs describe cancellation limits and race behavior honestly.

## Handoff Notes For The Next Step

The next step adds observability routes. It can use the new job index for queue
summaries if useful, but should not change cancellation semantics.
