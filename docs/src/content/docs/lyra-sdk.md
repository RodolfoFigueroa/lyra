---
title: lyra-sdk
description: Python SDK contracts for Lyra plugin runtimes, job models, spatial input types, and database access.
---

`lyra-sdk` contains the public Python contracts shared by Lyra plugins, API
clients, and the worker runtime. Plugin code should import runtime types from
this package instead of importing from `lyra_app`.

## Common Imports

```python
from lyra.sdk import LyraDB, RunContext
from lyra.sdk.models import FileJobResult, JobEnvelope, TableJobResult
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.types import ExplicitBoundsAPI, ExplicitLocationAPI
```

Use `lyra.sdk` for the most common runtime symbols, `lyra.sdk.models` for
Pydantic models, `lyra.sdk.models.geometry` for GeoJSON models, and
`lyra.sdk.types` for explicit spatial input aliases.

## Runner Entry Points

Every runner plugin metric is a synchronous function that accepts a
`JobEnvelope` and a `RunContext`, then returns a terminal result model.

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import JobEnvelope, TableJobResult
from lyra.sdk.models.geometry import GeoJSON


def run(job: JobEnvelope, context: RunContext) -> TableJobResult:
    context.emit_event("progress", {"message": "Starting"})
    context.check_cancelled()

    location = GeoJSON.model_validate(job.input["location"])
    return TableJobResult(
        job_id=job.job_id,
        index=[feature.id for feature in location.features],
        columns=["value"],
        data=[[42] for _feature in location.features],
    )
```

`JobEnvelope` contains the validated job request:

| Field | Type | Purpose |
| --- | --- | --- |
| `job_id` | `str` | Stable job identifier assigned by Lyra. |
| `metric` | `str` | Metric name selected from the plugin manifest. |
| `input` | `dict[str, Any]` | Client payload after API-side JSON Schema validation. |
| `idempotency_key` | `str | None` | Optional client-supplied key. Lyra passes it through but does not deduplicate jobs. |
| `metadata` | `dict[str, Any]` | Optional metadata carried with the job envelope. |

`RunContext` exposes runtime services:

| Member | Purpose |
| --- | --- |
| `job_id` | Same job identifier as `job.job_id`. |
| `metric` | Current metric name. |
| `logger` | Metric-scoped Python logger. |
| `temp_dir` | Per-job directory for intermediate files and file outputs. |
| `db` | Optional `LyraDB` database interface. It can be `None`. |
| `emit_event(event, data=None)` | Appends a durable progress event and marks the job as `progress`. |
| `check_cancelled()` | Raises if the job has been cancelled so the worker can persist a cancelled result. |

## Returning Results

Successful value metrics return `TableJobResult`. The table must match the
metric manifest's `output.columns` and must be indexed by the resolved
`location` feature IDs.

Return table results with split-table fields:

```python
return TableJobResult(
    job_id=job.job_id,
    index=["area-1", "area-2"],
    columns=["mean_temperature"],
    data=[[24.8], [23.1]],
)
```

Return files by writing under `context.temp_dir` and returning `FileJobResult`:

```python
output_path = context.temp_dir / "result.tif"
# write output_path here

return FileJobResult(
    job_id=job.job_id,
    file_path=str(output_path),
    media_type="image/tiff",
)
```

Return expected plugin failures as `FailedJobResult`s:

```python
from lyra.sdk.models import FailedJobResult

return FailedJobResult(
    job_id=job.job_id,
    error={"type": "validation", "message": "Input geometry is empty"},
)
```

## LyraDB

`LyraDB` is the SDK database interface available as `context.db`. Plugin code
should use the object provided by the worker instead of constructing `LyraDB`
directly. The worker provides an
implementation when database settings are available.

Always handle the optional case:

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import FailedJobResult, JobEnvelope, TableJobResult
from lyra.sdk.models.geometry import GeoJSON
from lyra.utils.geometry import convert_geojson_to_gdf


def run(job: JobEnvelope, context: RunContext) -> TableJobResult | FailedJobResult:
    if context.db is None:
        return FailedJobResult(
            job_id=job.job_id,
            error={"type": "configuration", "message": "Database is unavailable"},
        )

    location = GeoJSON.model_validate(job.input["location"])
    gdf = convert_geojson_to_gdf(location)
    xmin, ymin, xmax, ymax = gdf.total_bounds
    census = context.db.load_census_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        level="ageb",
        columns=["pobtot", "geometry"],
    )

    return TableJobResult(
        job_id=job.job_id,
        index=[str(feature_id) for feature_id in gdf.index],
        columns=["census_rows"],
        data=[[len(census)] for _feature_id in gdf.index],
    )
```

Available methods:

| Method | Required choices | Returns |
| --- | --- | --- |
| `load_denue_from_bounds(xmin, ymin, xmax, ymax, *, year, month)` | `year`: `2020` to `2025`; `month`: `5` or `11` | GeoDataFrame columns `per_ocu`, `codigo_act`, `geometry`. |
| `load_mesh_from_bounds(xmin, ymin, xmax, ymax, *, level=9)` | `level`: `4` to `9` | GeoDataFrame columns `codigo`, `geometry`. |
| `load_census_from_bounds(xmin, ymin, xmax, ymax, *, level, columns)` | `level`: `ent`, `mun`, `loc`, `ageb`, or `mza` | GeoDataFrame with requested columns and `geometry`. |

The bounding box arguments are `xmin`, `ymin`, `xmax`, and `ymax`.

## Explicit Spatial Inputs

`ExplicitLocationAPI` and `ExplicitBoundsAPI` are marker aliases for helper
function signatures. They document whether a helper expects resolved
client-selected features (`GeoJSON`) or one resolved enclosing geometry
(`SingleGeoJSON`).

All metrics declare spatial fields in `spatial_inputs`. Clients submit wrappers
for those fields, and Lyra resolves them before the worker calls the runner.
Runner code parses the resolved `job.input` field into `GeoJSON` or
`SingleGeoJSON`.

```python
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.types import ExplicitLocationAPI
from lyra.utils.geometry import convert_geojson_to_gdf


def summarize_locations(locations: ExplicitLocationAPI) -> dict[str, int]:
    gdf = convert_geojson_to_gdf(locations)
    return {"feature_count": len(gdf)}


def parse_location(input_payload: dict) -> GeoJSON:
    return GeoJSON.model_validate(input_payload["location"])


def parse_bounds(input_payload: dict) -> SingleGeoJSON:
    return SingleGeoJSON.model_validate(input_payload["bounds"])
```

Underlying models:

| Alias | Underlying SDK model | Use when |
| --- | --- | --- |
| `ExplicitLocationAPI` | `GeoJSON` | The metric should run over one or more client-selected features. |
| `ExplicitBoundsAPI` | `SingleGeoJSON` | The metric needs one enclosing area or bounding geometry. |

Client spatial payloads use wrapper objects with a `data_type` discriminator and
a `value`. Lyra validates and resolves those wrappers before constructing the
`JobEnvelope`. For the full wrapper lifecycle, request shapes, and sample jobs,
see [Spatial Plugin Inputs](../spatial-plugin-inputs/).

`GET /data_types` and `client.get_data_types()` expose grouped wrapper schemas:

| Group | Use when |
| --- | --- |
| `location` | The metric accepts one or more client-selected features. |
| `bounds` | The metric accepts one enclosing area or bounding geometry. |

Each item contains `data_type`, `description`, and `wrapper_schema`. Fetch
`GET /metrics/{metric_name}` for the complete metric request schema with
wrappers injected into the declared spatial fields.

## Geometry Models

The SDK geometry models represent GeoJSON FeatureCollections with an explicit
CRS object.

| Model | Purpose |
| --- | --- |
| `GeoJSON` | FeatureCollection with one or more `Feature` objects. Features may contain `Point`, `Polygon`, or `MultiPolygon` geometry. |
| `SingleGeoJSON` | FeatureCollection with exactly one feature. The feature may contain `Point` or `Polygon` geometry. |
| `Feature` | GeoJSON feature with required non-empty `id`, geometry, and `properties`. |
| `CRS` | GeoJSON CRS object shaped as `{ "type": "name", "properties": { "name": "EPSG:4326" } }`. |

Use `lyra-utils` when you need to convert these models to GeoDataFrames.

## SDK Models

Most plugin code only needs `JobEnvelope`, `RunContext`, `TableJobResult`, and
`FileJobResult`, but
these models are also available for tests, clients, and manifest tooling:

| Model | Where it is used |
| --- | --- |
| `JobCreateRequest` | Public `/jobs` request body. |
| `JobCreateResponse` | Public `/jobs` submission response. |
| `JobStatusInfo` | Public `/jobs/{job_id}` status response. |
| `JobEvent` | Durable event payload streamed from `/jobs/{job_id}/events`. |
| `TableJobResult` | Successful terminal result for value metrics. |
| `FileJobResult` | Successful terminal result for file metrics. |
| `FailedJobResult` | Terminal result for expected or runtime failures. |
| `CancelledJobResult` | Terminal result persisted by the worker for cancelled jobs. |
| `MetricInfoV2` | Public metric catalog item exposed by `/metrics`. |
| `PluginManifestV2` | Strict v2 `lyra.plugin.json` manifest model. |
| `MetricManifestV2` | One metric entry inside a plugin manifest. |
