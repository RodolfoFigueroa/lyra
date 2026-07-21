---
title: Operator Runbook
description: Monitor health, retained jobs, queues, workers, plugins, and cancellation.
---

## Health

`GET /live` is dependency-free process liveness. `GET /ready` checks Redis and
PostGIS concurrently and returns `503` when either is unavailable. Use liveness
for process restart and readiness for traffic and worker startup gates.

Admin status, config summary, catalog, workers, queues, and recent jobs provide
the operational view. Config summaries omit secrets. Worker inspection is
sampled in the API process; responses report observation time, age, staleness,
and errors instead of pretending stale values are current.

## Retained jobs

Each job has Redis status, result, event, provenance, and associated idempotency
records. They expire with `job_store.ttl_seconds`. Downloads do not extend
retention. A missing job after expiry is expected and cannot be reconstructed
from Redis.

SSE events replay from retained history, support `Last-Event-ID`, and close on a
terminal event. Each stream is approximately capped by
`job_events.max_stream_events`; status projections remain available if older
events have been trimmed. Queue depth may be explicitly unknown when broker
inspection is unavailable.

## Structured logs

Lyra writes JSON Lines to standard output or `logging.file`. Every record has a
UTC timestamp, level, logger, and message. Published job events add structured
fields including `event_kind`, `job_id`, `metric`, and the relevant lifecycle,
progress, or message fields. Lifecycle changes, progress stage boundaries and
completion, and plugin messages at info or above are operational records;
ordinary intermediate progress is debug-level. Configure collection as JSON
rather than parsing human-readable message strings.

## Cancellation and interruption

`POST /admin/jobs/{job_id}/cancel` marks active work cancelled, appends an event,
and asks Celery to revoke the task. Cancellation is cooperative after plugin
code begins. Terminal results win races and are not overwritten.

Worker interruption and plugin failure are normalized into terminal result
records so consumers keep using the same result endpoint. Unexpected Celery
task failures are recorded by the surviving worker parent. Job reads also
repair nonterminal Lyra state when Celery's result backend already reports a
failure. A complete worker or host loss that leaves Celery without a terminal
state is not inferred automatically.

## Common response

1. Check `/live` and `/ready`.
2. Inspect admin status and its observation metadata.
3. Confirm the metric exists in the catalog and has a queue assignment.
4. Confirm at least one observed worker consumes that queue.
5. Inspect worker startup/install logs for plugin failures.
6. Check job events and terminal error details.
7. Refresh the catalog and restart workers only when source or routing changed.

Do not restart workers repeatedly to compensate for an invalid manifest,
unreachable source, missing database data, or incompatible plugin package.
