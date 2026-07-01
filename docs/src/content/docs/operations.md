---
title: Operations
description: Understand Redis job storage, event streams, TTLs, cancellation, and worker interruption behavior.
---

Lyra uses Redis for Celery transport and for the job store.

The Redis URL comes from `[redis].url` in `/lyra_data/config/lyra.toml`. The
same URL is used by Celery and by the async/sync Redis clients.

## Job Store Keys

Each job uses three Redis key families:

- `job:{job_id}:status`: JSON status snapshot.
- `job:{job_id}:result`: normalized terminal result JSON.
- `job:{job_id}:events`: Redis Stream of `JobEvent` payloads.

Status snapshots include:

- `job_id`
- optional `metric`
- `status`
- `updated_at`
- optional `error`

## TTL

Job store keys use `[job_store].ttl_seconds`. The default is `600` seconds.

File result cleanup deletes only `job:{job_id}:result` after the file response
completes. Status and events remain until their TTL expires.

## Events

Lifecycle transitions append events:

- Job creation appends `queued`.
- Worker start appends `started`.
- `RunContext.emit_event()` appends plugin progress events and sets status to `progress`.
- Terminal result persistence appends `succeeded`, `failed`, or `cancelled`.

The `/jobs/{job_id}/events` route replays stored events, supports
`Last-Event-ID`, blocks for live updates, and closes after a terminal event.

## Cancellation

Cancellation storage is represented by the job status. A runner that calls
`context.check_cancelled()` will stop if the status is `cancelled`; the worker
persists a terminal cancelled result.

Operators can request cancellation with
`POST /admin/jobs/{job_id}/cancel`. The route requires admin Bearer auth, marks
active `queued`, `started`, or `progress` jobs as `cancelled`, emits a
cancellation event, and asks Celery to revoke the task by job ID.

Cancellation is cooperative once plugin code is running. A job that already
persisted a terminal `succeeded`, `failed`, or `cancelled` status is not
overwritten; the admin route returns `409` instead. If a job finishes while a
cancellation request is racing with it, the terminal result remains the source
of truth.

## Interrupted Workers

Worker interruption handling writes failed terminal result records through the same
job store. This keeps result consumers on the `/jobs/{job_id}/result` path
for terminal JSON metadata regardless of whether failure came from plugin code,
validation, or worker shutdown handling. File bytes are served separately from
`/jobs/{job_id}/result/download`.

## Observability

`GET /health` is public and reports API liveness plus Redis readiness. Admin
observability routes require Bearer auth:

- `GET /admin/status`
- `GET /admin/config-summary`
- `GET /admin/catalog`
- `GET /admin/workers`
- `GET /admin/workers/{worker_name}`
- `GET /admin/queues`

Worker and queue routes are defensive around Celery inspect. If workers are
offline or Celery does not answer, worker state may be `unknown` and queue
`pending_depth` is returned as `null` with `pending_depth_unknown: true` instead
of guessing. Config summaries intentionally omit secrets and raw environment
variables.
