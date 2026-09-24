---
title: Operator Runbook
description: Monitor health, retained jobs, queues, workers, plugins, and cancellation.
---

Use the [administrative CLI](../admin-cli/) for one-shot inspection and management.
For example, run `uv run lyra-admin health`, then authenticated
`uv run lyra-admin workers list` and `uv run lyra-admin queues list`.

## Health

`GET /live` is dependency-free process liveness. `GET /ready` checks Redis and
PostGIS concurrently and also checks startup catalog availability. It returns
`503` when any of these is unavailable. Use liveness
for process restart and readiness for traffic and worker startup gates.

Admin status, config summary, catalog, workers, queues, and recent jobs provide
the operational view. Config summaries omit secrets. Worker inspection is
sampled in the API process; responses report observation time, age, staleness,
and errors instead of pretending stale values are current.

## Retained jobs

Queued and running jobs have no expiration. Silent jobs remain visible even
without connected clients or progress reports. After the first terminal
transition, status, result, provenance, and idempotency records share one
expiration deadline: `job_store.result_retention_seconds` (default 86400).
Reads, duplicate deliveries, and late updates never extend it. Pre-acceptance
idempotency reservations are bounded by the same configured duration.

Retention controls Redis records and API access. Output files remain on disk;
operators own filesystem cleanup. No automatic filesystem sweeper runs.

## Structured logs

Lyra writes JSON Lines to standard output or `logging.file`. Every record has a
UTC timestamp, level, logger, and message. Lifecycle transitions are logged
independently of progress. Plugin `context.logger` adds `job_id` and `metric`
automatically and supports normal logging arguments. Unexpected exceptions
include full tracebacks in logs; public results contain concise error messages.

## Cancellation and interruption

`POST /admin/jobs/{job_id}/cancel` atomically saves cancelled status and its terminal result,
and asks Celery to revoke the task. Cancellation is cooperative after plugin
code begins. Terminal results win races and are not overwritten.

Worker interruption and plugin failure are normalized into terminal result
records so consumers keep using the same result endpoint. Unexpected Celery
task failures are recorded by the surviving worker parent. Job reads also
repair nonterminal Lyra state when Celery's result backend already reports a
failure. A complete worker or host loss that leaves Celery without a terminal
state is not inferred automatically. Such unresolved active jobs remain retained
until reconciliation or administrative cancellation. Operators must investigate
worker loss; absent progress alone never indicates failure. `completed_at` is the
terminal state transition time and does not mean cancelled computation has stopped.

## Common response

1. Check `/live` and `/ready`.
2. Inspect admin status and its observation metadata.
3. Confirm the metric exists in the catalog and has a queue assignment.
4. Confirm at least one observed worker consumes that queue.
5. Inspect worker startup/install logs for plugin failures.
6. Check job status, diagnostic logs, and terminal error details.
7. Correct the TOML or plugin, drain jobs, and restart the API and all workers.

Do not restart workers repeatedly to compensate for an invalid manifest,
unreachable source, missing database data, or incompatible plugin package.
