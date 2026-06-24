---
title: Runner Plugins
description: Implement v2 runner entrypoints with JobEnvelope, RunContext, and JobResult.
---

Worker processes install runner plugin code at startup, read v2 manifests, import each matching entrypoint, and register the imported function under the generic `lyra.run_metric` task registry.

## Entrypoint Contract

Each metric entrypoint must expose a sync function:

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import JobEnvelope, JobResult


def run(job: JobEnvelope, context: RunContext) -> JobResult:
    context.emit_event("progress", {"message": "Starting"})
    context.check_cancelled()
    return JobResult(
        job_id=job.job_id,
        status="succeeded",
        result={"value": 42},
    )
```

The worker calls `run(job, context)` and validates the returned `JobResult`. Unknown metrics, plugin exceptions, invalid return payloads, and mismatched result `job_id` values become failed `JobResult`s.

## JobEnvelope

`JobEnvelope` contains:

- `job_id`
- `metric`
- `input`
- optional `idempotency_key`
- `metadata`

The `input` payload has already passed API-side JSON Schema validation before dispatch.

## RunContext

`RunContext` exposes:

- `job_id`
- `metric`
- `logger`
- `temp_dir`
- optional `db`
- `emit_event(event, data=None)`
- `check_cancelled()`

`emit_event()` appends a durable `JobEvent` to the Redis Stream and marks the job status as `progress`.

`check_cancelled()` raises an internal worker cancellation signal if the job status is already `cancelled`; the worker then persists a terminal cancelled result.

Use `temp_dir` for intermediate files. For file results, return a `JobResult` with `result_type="file"` and `file_path` set to the produced file.

## JobResult

Terminal statuses are:

- `succeeded`
- `failed`
- `cancelled`

JSON result example:

```python
JobResult(job_id=job.job_id, status="succeeded", result={"value": 42})
```

File result example:

```python
JobResult(
    job_id=job.job_id,
    status="succeeded",
    result_type="file",
    file_path=str(output_path),
)
```

Failed result example:

```python
JobResult(
    job_id=job.job_id,
    status="failed",
    error={"type": "validation", "message": "Input geometry is empty"},
)
```
