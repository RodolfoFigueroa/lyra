---
title: Runner Plugins
description: Implement schema v3 runner entrypoints with JobEnvelope, RunContext, and terminal result models.
---

Runner plugins are the code that workers actually execute. At startup, each
worker installs plugin code, reads schema v3 manifests, imports matching
entrypoints, and runs matching metrics through the generic `lyra.run_metric`
Celery task.

Plugin sources are trusted code. The API reads manifests without importing
plugin modules, but workers install and execute plugin packages with the worker
container's permissions.

For the Python package surface available to plugin code, see the
[lyra-sdk](../lyra-sdk/) and [lyra-utils](../lyra-utils/) references. For
publish-time checks, see [Plugin Author Checklist](../plugin-author-checklist/).

## Worker Install And Import

Workers sync enabled plugin sources from Lyra-owned plugin state, run
`uv pip install --dry-run`, install compatible packages editable, and import
metrics selected by the worker's queue list. Start workers with
`python -m lyra_app.worker_launcher <name>`; the launcher reads
`[workers.<name>]`, filters metrics by `/lyra_data/state/plugins.toml`, and
starts Celery with matching `-Q` and concurrency values.

If a plugin fails compatibility checks or editable install, that worker skips
the plugin. The API can still expose the metric when its manifest is valid, so
worker logs are the best place to diagnose install and import problems.

A manifest parse failure for any installed plugin, duplicate selected metric
name, or import failure for a selected entrypoint prevents the worker registry
from loading. Verify each selected entrypoint with the preflight import command
before publishing the branch or tag that Lyra will run.

## Entrypoint Contract

Each metric entrypoint must expose a sync function. Return `TableJobResult` for
value metrics and `FileJobResult` for file-producing metrics:

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import JobEnvelope, TableJobResult
from lyra.sdk.models.geometry import GeoJSON


def run(job: JobEnvelope, context: RunContext) -> TableJobResult:
    context.emit_event("progress", {"message": "Starting"})
    context.check_cancelled()

    location = GeoJSON.model_validate(job.input["location"])
    return TableJobResult.from_mapping(
        job_id=job.job_id,
        input_index=[feature.id for feature in location.features],
        columns=["value"],
        values={"value": [42 for _feature in location.features]},
    )
```

The worker calls `run(job, context)` and validates the returned terminal result
against the metric's manifest `output` declaration.

Unknown metrics, plugin exceptions, invalid return payloads, and mismatched
result `job_id` values become `FailedJobResult` payloads.

For expected domain failures, such as an empty input geometry, plugins may
return `FailedJobResult`. Plugins should usually not return
`CancelledJobResult`; call `context.check_cancelled()` and let the worker persist
the cancelled result.

## JobEnvelope

`JobEnvelope` contains:

- `job_id`
- `metric`
- `input`
- optional `idempotency_key`
- `metadata`
- optional server-calculated `location_areas_m2`

The `input` payload has already passed API-side JSON Schema validation before
dispatch. Spatial wrapper fields have also been resolved by the API, so
`job.input` contains canonical GeoJSON dictionaries under the manifest's
declared `inputs` field names. Parse those fields with
`GeoJSON.model_validate()` or `SingleGeoJSON.model_validate()` before using
`lyra-utils`.

`location_areas_m2` is execution metadata populated for metrics that declare
fractional-area derivations. Plugins do not calculate or return those derived
columns themselves.

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

For file results, write the artifact under `context.temp_dir` and return
`FileJobResult`.

For `LyraDB` methods, explicit spatial input aliases such as
`ExplicitLocationAPI` and `ExplicitBoundsAPI`, and SDK geometry models, see
[lyra-sdk](../lyra-sdk/).

## Terminal Results

Table result constructors:

```python
TableJobResult.from_mapping(
    job_id=job.job_id,
    input_index=gdf.index,
    columns=["area_m2"],
    values={"area_m2": area_by_feature_id},
)
```

If `area_m2` declares a `fraction_of_location_area` derivation, Lyra validates
this runner result and then appends the derived fraction column.

```python
TableJobResult.from_dataframe(
    job_id=job.job_id,
    dataframe=summary_dataframe,
)
```

```python
TableJobResult.from_series(
    job_id=job.job_id,
    series=area_by_feature,
    name="area_m2",
)
```

Use the constructor that matches your metric output: `from_mapping()` for
mapping or sequence values, `from_dataframe()` for table-shaped Pandas or
GeoPandas results, and `from_series()` for one-column Pandas results. The helper
constructors serialize result indices to strings and reject duplicate
stringified axes.

File result example:

```python
from lyra.sdk.models import FileJobResult

FileJobResult(
    job_id=job.job_id,
    file_path=str(output_path),
    media_type="image/tiff",
)
```

Failed result example:

```python
from lyra.sdk.models import FailedJobResult

FailedJobResult(
    job_id=job.job_id,
    error={"type": "validation", "message": "Input geometry is empty"},
)
```

Successful table results use a split-table wire shape with `index`, `columns`,
and row-major `data`. The worker requires `index` to match the resolved
`location` feature IDs after string conversion and `columns` to match the
manifest output declaration exactly. For table outputs with `batched_columns`,
the worker expands those columns from the validated source array first. A
manifest with `name: "job_accessibility_{key}"` and input
`sector_filters: [{"key": "sectors_091_092", "value": "^09[12].*"}]` must
return column `job_accessibility_sectors_091_092`. The plugin uses each
batched item's `value` for computation, but Lyra uses `key` for column names
and optional `label` for descriptions.

For deciding between static columns, batched columns, file outputs, separate
jobs, and separate metrics, see
[Metric Output Design](../metric-output-design/).
