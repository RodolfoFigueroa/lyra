---
title: Operations
description: Understand Redis job storage, event streams, TTLs, cancellation, and worker interruption behavior.
---

Lyra uses Redis for Celery transport and for the v2 job store.

The Redis URL comes from `CELERY_BROKER_URL`. The same URL is used by Celery and
by the async/sync Redis clients.

## Job Store Keys

Each job uses three Redis key families:

- `job:{job_id}:status`: JSON status snapshot.
- `job:{job_id}:result`: normalized terminal `JobResult` JSON.
- `job:{job_id}:events`: Redis Stream of `JobEvent` payloads.

Status snapshots include:

- `job_id`
- optional `metric`
- `status`
- `updated_at`
- optional `error`

## TTL

Job store keys use `LYRA_JOB_STORE_TTL_SECONDS`. The default is `600` seconds.

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
persists a terminal cancelled `JobResult`.

There is no public cancellation endpoint in the current API.

## Interrupted Workers

Worker interruption handling writes failed `JobResult` records through the same
job store. This keeps result consumers on the `/jobs/{job_id}/result` path
regardless of whether failure came from plugin code, validation, or worker
shutdown handling.
