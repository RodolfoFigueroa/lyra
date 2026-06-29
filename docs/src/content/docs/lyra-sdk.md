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
from lyra.sdk.models import JobEnvelope, JobResult
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.types import ExplicitBoundsAPI, ExplicitLocationAPI
```

Use `lyra.sdk` for the most common runtime symbols, `lyra.sdk.models` for
Pydantic models, `lyra.sdk.models.geometry` for GeoJSON models, and
`lyra.sdk.types` for explicit spatial input aliases.

## Runner Entry Points

Every runner plugin metric is a synchronous function that accepts a
`JobEnvelope` and a `RunContext`, then returns a `JobResult`.

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

`JobResult.status` is terminal and must be one of `succeeded`, `failed`, or
`cancelled`.

Return JSON results in `result`:

```python
return JobResult(
    job_id=job.job_id,
    status="succeeded",
    result={"mean_temperature": 24.8},
)
```

Return files by writing under `context.temp_dir` and setting `result_type` and
`file_path`:

```python
output_path = context.temp_dir / "result.tif"
# write output_path here

return JobResult(
    job_id=job.job_id,
    status="succeeded",
    result_type="file",
    file_path=str(output_path),
)
```

Return expected plugin failures as failed `JobResult`s:

```python
return JobResult(
    job_id=job.job_id,
    status="failed",
    error={"type": "validation", "message": "Input geometry is empty"},
)
```

## LyraDB

`LyraDB` is the SDK database interface available as `context.db`. Do not
construct `LyraDB` directly inside a plugin. The worker provides an
implementation when database settings are available.

Always handle the optional case:

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import JobEnvelope, JobResult
from lyra.sdk.models.geometry import SingleGeoJSON
from lyra.utils.geometry import convert_geojson_to_gdf


def run(job: JobEnvelope, context: RunContext) -> JobResult:
    if context.db is None:
        return JobResult(
            job_id=job.job_id,
            status="failed",
            error={"type": "configuration", "message": "Database is unavailable"},
        )

    bounds = SingleGeoJSON.model_validate(job.input["bounds"])
    bounds_gdf = convert_geojson_to_gdf(bounds)
    xmin, ymin, xmax, ymax = bounds_gdf.total_bounds
    census = context.db.load_census_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        level="ageb",
        columns=["pobtot", "geometry"],
    )

    return JobResult(
        job_id=job.job_id,
        status="succeeded",
        result={"rows": len(census)},
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

Accepted wrapper payloads use a discriminator named `data_type` and a `value`:

```json
{ "data_type": "cvegeo_list", "value": ["090020001", "090020002"] }
```

```json
{ "data_type": "met_zone_code", "value": "MET_ZONE_CODE" }
```

```json
{
  "data_type": "geojson",
  "value": {
    "type": "FeatureCollection",
    "features": [
      {
        "id": "area-1",
        "type": "Feature",
        "geometry": {
          "type": "Polygon",
          "coordinates": [
            [
              [-99.20, 19.30],
              [-99.10, 19.30],
              [-99.10, 19.40],
              [-99.20, 19.40],
              [-99.20, 19.30]
            ]
          ]
        },
        "properties": {}
      }
    ],
    "crs": { "type": "name", "properties": { "name": "EPSG:4326" } }
  }
}
```

`GET /data_types` or `client.get_data_types()` returns grouped wrapper schemas:

| Group | Use when |
| --- | --- |
| `location` | The metric accepts one or more client-selected features. |
| `bounds` | The metric accepts one enclosing area or bounding geometry. |

Each item contains `data_type`, `description`, and `wrapper_schema`. For a full
plugin example, see [Spatial Plugin Inputs](../spatial-plugin-inputs/). Fetch
`GET /metrics/{metric_name}` for the complete metric request schema with these
wrappers injected into the declared spatial fields.

```python
from lyra.sdk.types import ExplicitBoundsAPI
from lyra.utils.geometry import convert_geojson_to_gdf


def summarize_bounds(bounds: ExplicitBoundsAPI) -> dict[str, float]:
    gdf = convert_geojson_to_gdf(bounds)
    xmin, ymin, xmax, ymax = gdf.total_bounds
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
```

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

Most plugin code only needs `JobEnvelope`, `RunContext`, and `JobResult`, but
these models are also available for tests, clients, and manifest tooling:

| Model | Where it is used |
| --- | --- |
| `JobCreateRequest` | Public `/jobs` request body. |
| `JobCreateResponse` | Public `/jobs` submission response. |
| `JobStatusInfo` | Public `/jobs/{job_id}` status response. |
| `JobEvent` | Durable event payload streamed from `/jobs/{job_id}/events`. |
| `MetricInfoV2` | Public metric catalog item exposed by `/metrics`. |
| `PluginManifestV2` | Strict v2 `lyra.plugin.json` manifest model. |
| `MetricManifestV2` | One metric entry inside a plugin manifest. |
