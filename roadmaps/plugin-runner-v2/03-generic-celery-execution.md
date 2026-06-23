# Step 3 - Generic Celery Execution

## Goal

Replace per-metric Celery task registration with one generic task, `lyra.run_metric`, that executes a `JobEnvelope`.

## Key Changes

- Register exactly one Celery task:
  - task name: `lyra.run_metric`
  - payload: `JobEnvelope`
- Worker startup flow:
  - sync/install plugin repos
  - read v2 manifests
  - filter metrics by `LYRA_RUNNER_QUEUES`
  - import each metric entrypoint
  - build a local metric registry keyed by metric name
- Task execution flow:
  - receive `JobEnvelope`
  - look up the metric in the local worker registry
  - build `RunContext`
  - call the configured `run(job, context)` entrypoint
  - persist/publish the returned `JobResult`
- Remove worker-side dynamic Pydantic model generation from plugin function signatures.
- Remove special wrapper branches for batched metrics and file-returning metrics.
- Batching, Earth Engine polling, file production, and progress become plugin-internal behavior exposed through `RunContext`.

## Tests

- Worker registers only `lyra.run_metric`.
- Worker imports only metrics whose execution queue matches `LYRA_RUNNER_QUEUES`.
- Generic task executes the correct metric entrypoint.
- Generic task handles unknown metrics as worker errors.
- Plugin exceptions become failed job results.
- File result payloads are persisted through the same generic result path.

## Done Criteria

- No Celery task is registered per metric.
- Worker execution uses the v2 manifest entrypoint only.
- All plugin-specific branching has moved out of core worker wrappers.
