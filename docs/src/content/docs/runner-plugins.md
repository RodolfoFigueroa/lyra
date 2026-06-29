---
title: Runner Plugins
description: Implement v2 runner entrypoints with JobEnvelope, RunContext, and JobResult.
---

Runner plugins are the code that workers actually execute. At startup, each
worker installs plugin code, reads v2 manifests, imports matching entrypoints,
and runs matching metrics through the generic `lyra.run_metric` Celery task.

Plugin repositories are trusted code. The API reads manifests without importing
plugin modules, but workers install and execute plugin packages with the worker
container's permissions.

For the Python package surface available to plugin code, see the
[lyra-sdk](../lyra-sdk/) and [lyra-utils](../lyra-utils/) references. For
publish-time checks, see [Plugin Author Checklist](../plugin-author-checklist/).

## Worker Install And Import

Workers sync configured repositories, run `uv pip install --dry-run`, install
compatible packages editable, and import metrics selected by
`LYRA_RUNNER_QUEUES`. When `LYRA_RUNNER_QUEUES` is set, a worker imports only
metrics whose `execution.queue` is in that comma-separated list. When it is
unset, the worker imports every metric from installed plugins. Celery's `-Q`
setting still controls which queue messages that worker receives.

If a plugin fails compatibility checks or editable install, that worker skips
the plugin. The API can still expose the metric when its manifest is valid, so
worker logs are the best place to diagnose install and import problems.

A manifest parse failure for any installed plugin, duplicate selected metric
name, or import failure for a selected entrypoint prevents the worker registry
from loading. Verify each selected entrypoint with the preflight import command
before publishing the branch or tag that Lyra will run.

## Entrypoint Contract

Each metric entrypoint must expose a sync function. The documented return value
is `JobResult`:

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

The worker calls `run(job, context)` and validates the returned `JobResult`.
Internally it can normalize compatible dictionaries as `JobResult`, but plugin
authors should return `JobResult` directly so type checks and tests catch shape
errors early.

Unknown metrics, plugin exceptions, invalid return payloads, and mismatched
result `job_id` values become failed `JobResult`s.

The worker validates the returned object as `JobResult`; it does not validate
`result` against the metric's `result_schema`. Treat `result_schema` as
client-facing metadata and cover important output-shape checks in plugin tests.

## JobEnvelope

`JobEnvelope` contains:

- `job_id`
- `metric`
- `input`
- optional `idempotency_key`
- `metadata`

The `input` payload has already passed API-side JSON Schema validation before
dispatch. Spatial wrapper fields have also been resolved by the API, so
`job.input` contains canonical GeoJSON dictionaries under the manifest's
declared spatial field names. Parse those fields with `GeoJSON.model_validate()`
or `SingleGeoJSON.model_validate()` before using `lyra-utils`.

## RunContext

`RunContext` exposes:

- `job_id`
- `metric`
- `logger`
- `temp_dir`
- optional `db`
- `emit_event(event, data=None)`
- `check_cancelled()`

`emit_event()` appends a durable `JobEvent` to the Redis Stream and marks the
job status as `progress`.

Use non-terminal event names for plugin progress, such as `progress`, `loaded_input`, or `export_started`.

`check_cancelled()` raises an internal worker cancellation signal if the job
status is already `cancelled`; the worker then persists a terminal cancelled
result.

Use `temp_dir` for intermediate files. The worker creates a per-job directory before calling the plugin.

`db` is optional. Plugins should handle `context.db is None` gracefully.

For file results, return a `JobResult` with `result_type="file"` and
`file_path` set to the produced file.

For `LyraDB` methods, explicit spatial input aliases such as
`ExplicitLocationAPI` and `ExplicitBoundsAPI`, and SDK geometry models, see
[lyra-sdk](../lyra-sdk/).

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
