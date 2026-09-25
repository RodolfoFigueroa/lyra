---
title: Operator Runbook
description: Monitor health, retained jobs, queues, workers, and plugins.
---

Use the [administrative CLI](../admin-cli/) for one-shot inspection and management.
For example, run `uv run lyra-admin health`, then authenticated
`uv run lyra-admin workers list` and `uv run lyra-admin queues list`.

## Health

`GET /live` is dependency-free process liveness. `GET /ready` checks Redis and
PostGIS concurrently and also checks startup catalog availability. It returns
`503` when any of these is unavailable. Use liveness
for process restart and readiness for traffic gates.

Admin status, config summary, catalog, workers, queues, and queue/status job pages
provide the operational view. Config summaries omit secrets. Worker responses
report native IDs, hostnames, queues, current jobs, and heartbeat age. Configured
pools are listed separately from observed worker processes. Redis errors are
reported as unavailable, rather than as an empty queue or offline worker.

## Retained jobs

Queued jobs have no queue-age expiration. Running jobs use RQ's native execution
timeout (`jobs.execution_timeout_seconds`, default 18000). Successes and failures
remain available for `jobs.result_retention_seconds` (default 86400).
Retention controls Redis records and API access. Output files remain on disk;
no cleanup command, scheduler, or filesystem sweeper runs.

`GET /admin/jobs` requires a configured `queue` and one of `queued`, `running`,
`succeeded`, or `failed`. It accepts `offset` and `limit` (default 50, maximum 200).
Queued jobs use FIFO order; running jobs use registry order; completed and failed
jobs use descending registry order. Stale entries and transitions can shorten
pages. There is no global chronological ordering or metric filter.

`GET /admin/jobs/{job_id}` exposes status, queue, worker identity, and administrator-only
failure diagnostics. Public errors remain sanitized, with a generic fallback
when structured error metadata could not be saved.

## Structured logs

Lyra writes JSON Lines to standard output or `logging.file`. Every record has a
UTC timestamp, level, logger, and message. Lifecycle transitions are logged
independently of progress. Plugin `context.logger` adds `job_id` and `metric`
automatically and supports normal logging arguments. Unexpected exceptions
include full tracebacks in logs; public results contain concise error messages.

## Worker interruption

RQ supervises execution children and worker processes. Workers use a 30-second TTL,
a 5-second monitoring interval, and a 15-second maintenance interval. Abandoned
jobs should be detected within approximately two minutes when a worker serving
the queue is available for maintenance. Outages and full worker saturation can
delay detection. API reads never run cleanup or repair job records.

Progress is optional and best effort; lifecycle state is the completion signal.
Local wait interruption leaves the remote job running. Jobs are not replayed
automatically after a failure.

## Celery cutover

Stop submissions and drain existing Celery jobs or explicitly abandon them.
Deploy matching API, SDK, schema-4 configuration, and RQ worker images together.
Do not flush Redis or migrate old job records automatically. Legacy result
references are unsupported after cutover; retained files remain untouched.
Allow at least five hours plus shutdown overhead for active workers to finish.

## Common response

1. Check `/live` and `/ready`.
2. Inspect admin status and its observation metadata.
3. Confirm the metric exists in the catalog and has a queue assignment.
4. Confirm at least one observed worker consumes that queue.
5. Inspect worker startup logs for plugin failures.
6. Check job status, diagnostic logs, and terminal error details.
7. Correct the TOML or plugin, drain jobs, and restart the API and all workers.

Do not restart workers repeatedly to compensate for an invalid manifest,
missing installed distribution, missing database data, or incompatible plugin package.
