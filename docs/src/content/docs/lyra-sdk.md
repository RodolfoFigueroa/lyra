---
title: lyra-sdk
description: Python SDK contracts for Lyra plugin runtimes, job models, spatial input types, and database access.
---

`lyra-sdk` contains the public Python contracts shared by Lyra plugins, API
clients, and the worker runtime. Plugin code should import runtime types from
this package instead of importing from `lyra_app`.

## Common Imports

```python
from lyra.sdk import LocationInput, LyraDB, PluginDefinition, RunContext
from lyra.sdk.models import FileJobResult, TableJobResult
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.types import ExplicitBoundsAPI, ExplicitLocationAPI
```

Use `lyra.sdk` for the most common runtime symbols, `lyra.sdk.models` for
Pydantic models, `lyra.sdk.models.geometry` for GeoJSON models, and
`lyra.sdk.types` for explicit spatial input aliases.

## Typed Metric Functions

Every plugin owns one `PluginDefinition`. Its decorator derives the request
contract from a synchronous function's annotations and returns that function
unchanged.

```python
from lyra.sdk import LocationInput, PluginDefinition, RunContext
from lyra.sdk.models import TableJobResult
from lyra.sdk.models.plugin_v3 import TableOutputColumnV3, TableOutputV3

plugin = PluginDefinition()


@plugin.metric(
    name="example_metric",
    description="Return one value per input feature.",
    output=TableOutputV3(
        kind="table",
        columns=[TableOutputColumnV3(
            name="value",
            type="integer",
            unit="count",
            description="Example value.",
        )],
    ),
)
def calculate(
    location: LocationInput,
    *,
    context: RunContext,
) -> TableJobResult:
    context.emit_event("progress", {"message": "Starting"})
    context.check_cancelled()
    return TableJobResult.from_mapping(
        job_id=context.job_id,
        input_index=[feature.id for feature in location.features],
        columns=["value"],
        values={"value": [42 for _feature in location.features]},
    )
```

`RunContext` exposes runtime services:

| Member | Purpose |
| --- | --- |
| `job_id` | Stable job identifier assigned by Lyra. |
| `metric` | Current metric name. |
| `logger` | Metric-scoped Python logger. |
| `temp_dir` | Per-job directory for intermediate files and file outputs. |
| `db` | Optional `LyraDB` database interface. It can be `None`. |
| `emit_event(event, data=None)` | Appends a durable progress event and marks the job as `progress`. |
| `check_cancelled()` | Raises if the job has been cancelled so the worker can persist a cancelled result. |

## Returning Results

Successful value metrics return `TableJobResult`. The table must match the
metric manifest's `output.columns` and its serialized `index` must contain the
resolved `location` feature IDs as strings.

Choose the constructor that matches the shape your metric already produced:

| Method | Use when |
| --- | --- |
| `TableJobResult(...)` | You already have serialized `index`, `columns`, and row-major `data`. |
| `TableJobResult.from_mapping()` | Your values are keyed by the original input index, or by column-aligned sequences. |
| `TableJobResult.from_dataframe()` | Your metric already produced a Pandas or GeoPandas DataFrame. |
| `TableJobResult.from_series()` | Your metric produced one output column as a Pandas Series. |

All constructors validate table shape. The helper constructors convert index and
column labels to strings and reject duplicates after string conversion, such as
`1` and `"1"`.

Use `from_dataframe()` when your metric returns a table object:

```python
summary = compute_summary_dataframe(gdf)

return TableJobResult.from_dataframe(
    job_id=context.job_id,
    dataframe=summary,
)
```

Use `from_mapping()` when metric dictionaries are keyed by the original
`GeoDataFrame` index:

```python
area_by_feature = compute_area(gdf)

return TableJobResult.from_mapping(
    job_id=context.job_id,
    input_index=gdf.index,
    columns=["area_m2"],
    values={"area_m2": area_by_feature},
)
```

Use `from_series()` for one-column Pandas outputs:

```python
mean_temperature = compute_mean_temperature(gdf)

return TableJobResult.from_series(
    job_id=context.job_id,
    series=mean_temperature,
    name="mean_temperature",
)
```

Return files by writing under `context.temp_dir` and returning `FileJobResult`:

```python
output_path = context.temp_dir / "result.tif"
# write output_path here

return FileJobResult(
    job_id=context.job_id,
    file_path=str(output_path),
    media_type="image/tiff",
)
```

Return expected plugin failures as `FailedJobResult`s:

```python
from lyra.sdk.models import FailedJobResult

return FailedJobResult(
    job_id=context.job_id,
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
from lyra.sdk import LocationInput, RunContext
from lyra.sdk.models import FailedJobResult, TableJobResult
from lyra.utils.geometry import convert_geojson_to_gdf


def calculate(
    location: LocationInput,
    *,
    context: RunContext,
) -> TableJobResult | FailedJobResult:
    if context.db is None:
        return FailedJobResult(
            job_id=context.job_id,
            error={"type": "configuration", "message": "Database is unavailable"},
        )

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

    return TableJobResult.from_mapping(
        job_id=context.job_id,
        input_index=gdf.index,
        columns=["census_rows"],
        values={"census_rows": [len(census) for _feature_id in gdf.index]},
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

All metrics declare spatial fields in schema v3 `inputs` with
`kind: "location"` or `kind: "bounds"`. Clients submit wrappers for those
fields, and Lyra resolves them before the worker calls the runner. Runner code
passes parsed `GeoJSON` or `SingleGeoJSON` objects to decorated functions.

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
typed metric arguments. For the full wrapper lifecycle, request shapes, and sample jobs,
see [Spatial Plugin Inputs](../spatial-plugin-inputs/).

`GET /data-types` and `client.get_data_types()` expose grouped wrapper schemas:

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

Most plugin code only needs `PluginDefinition`, `LocationInput`, `RunContext`, `TableJobResult`, and
`FileJobResult`, but
these models are also available for tests, clients, and manifest tooling:

| Model | Where it is used |
| --- | --- |
| `JobCreateRequest` | Agent-authenticated `/jobs` request body. |
| `JobCreateResponse` | Agent-authenticated submission response, including idempotent reuse. |
| `JobStatusInfo` | Agent-authenticated `/jobs/{job_id}` status response. |
| `JobListResponse` | Admin `/admin/jobs` response. |
| `JobCancelResponse` | Admin `/admin/jobs/{job_id}/cancel` response. |
| `LivenessResponse` | Public dependency-free `/live` response. |
| `ReadinessResponse` | Public Redis and PostgreSQL `/ready` response. |
| `MetZoneCodeResponse` | Public `/lookups/met-zones` response. |
| `PluginRepoListResponse` and `PluginRepoResponse` | Admin plugin source list and repo records. |
| `CreatePluginRepoRequest` and `UpdatePluginRepoRequest` | Admin plugin source request bodies. |
| `CreatePluginRepoResponse`, `UpdatePluginRepoResponse`, `DeletePluginRepoResponse`, and `SyncPluginRepoResponse` | Admin plugin source operation responses with nested catalog refresh status. |
| `PluginCatalogRefreshStatus` | Catalog refresh status returned by plugin source mutations. |
| `PluginCatalogRefreshResponse` | Admin `/admin/plugin-catalog/refresh` response, including assigned and removed metric routes. |
| `WorkerRestartResponse` | Admin `/admin/workers/restart` response. |
| `PluginRoutingResponse` | Admin `/admin/plugin-routing` response. |
| `SetMetricQueueRequest`, `MetricQueueAssignmentResponse`, and `DeleteMetricQueueResponse` | Admin metric routing contracts. |
| `AdminStatusResponse` | Admin `/admin/status` response. |
| `ConfigSummaryResponse` | Admin `/admin/config-summary` response. |
| `CatalogSummaryResponse` | Admin `/admin/catalog` response. |
| `WorkersResponse` and `WorkerDetail` | Admin worker observability responses. |
| `QueuesResponse` | Admin `/admin/queues` response. |
| `JobEvent` | Durable event payload streamed from `/jobs/{job_id}/events`. |
| `TableJobResult` | Successful terminal result for value metrics. |
| `FileJobResult` | Successful terminal result for file metrics. |
| `FailedJobResult` | Terminal result for expected or runtime failures. |
| `CancelledJobResult` | Terminal result persisted by the worker for cancelled jobs. |
| `MetricCatalogResponse` | Public `/metrics` response with catalog fingerprint and metric items. |
| `MetricInfoV3` | Public metric catalog item exposed by `/metrics` and `/metrics/{metric_name}`. |
| `PluginManifestV3` | Strict model for the generated schema v3 deployment artifact. |
| `MetricManifestV3` | One metric entry inside a plugin manifest. |
| `compile_plugin_manifest()` | Compiler from compact authoring manifests to Lyra's runtime contract. |
