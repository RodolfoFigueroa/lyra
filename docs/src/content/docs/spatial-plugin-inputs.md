---
title: Spatial Plugin Inputs
description: Declare schema v3 spatial inputs and consume resolved GeoJSON in runner plugins.
---

Every Lyra metric has at least one spatial input. Plugin authors declare those
parameters with `LocationInput` or `BoundsInput`; manifest generation emits
`kind: "location"` or `kind: "bounds"`.
Lyra compiles the declarations into the wrapper schemas that clients see in
`/metrics`.

Spatial metadata is owned by Lyra. Declare `location: LocationInput` or
`bounds: BoundsInput` directly; the compiler adds canonical descriptions,
examples, and wrapper constraints. `Field` metadata on a spatial parameter is
rejected so every metric exposes the same spatial-input documentation.

Clients submit spatial fields through a wrapper object so the API can validate
and resolve them before a worker starts:

```json
{ "data_type": "geojson", "value": { "...": "FeatureCollection" } }
```

```json
{ "data_type": "cvegeo_list", "value": ["090020001", "090020002"] }
```

```json
{ "data_type": "met_zone_code", "value": "MET_ZONE_CODE" }
```

Raw GeoJSON belongs inside the `value` of the `geojson` wrapper. The manifest
does not define the wrapper shape; Lyra owns that protocol and injects the
canonical JSON Schema during compilation.

Wrapper payloads are strict. `cvegeo_list` values must contain CVEGEO strings of
one geographic level, so all strings must have the same valid length. A
`bounds` field using `data_type: "geojson"` must contain exactly one feature,
and that feature may be a point or polygon, not a multipolygon.

For a full plugin validation flow, see
[Plugin Author Checklist](../plugin-author-checklist/).

## Manifest Contract

Declare spatial fields inside the metric's `inputs` object:

| Input kind | Runner receives |
| --- | --- |
| `location` | `GeoJSON` with one or more features. |
| `bounds` | `SingleGeoJSON` with one enclosing geometry. |

```json
{
  "name": "average_temperature_by_location",
  "description": "Return average temperature for each submitted location.",
  "entrypoint": "temperature_plugin.runner:run",
  "inputs": {
    "location": { "kind": "location" },
    "year": {
      "kind": "integer",
      "minimum": 2020
    }
  },
  "output": {
    "kind": "table",
    "columns": [
      {
        "name": "mean_temperature",
        "type": "number",
        "unit": "C",
        "description": "Mean temperature for the input feature."
      }
    ]
  }
}
```

Fetch `GET /metrics/{metric_name}` to see the complete effective schema clients
need to submit. `GET /data-types` also exposes the individual wrapper schemas
for UI builders and client tooling.

Spatial inputs must be required. Table metrics must use an input named
`location` with `kind: "location"` because result rows are validated against
the resolved location feature IDs. File metrics can use whichever spatial input
shape they need.

## API Resolution

`POST /jobs` validates client input against the effective metric schema. Before
dispatching the job, Lyra resolves every declared spatial wrapper:

| Client wrapper | Resolution |
| --- | --- |
| `geojson` | Validates and forwards the supplied FeatureCollection. |
| `cvegeo_list` | Loads matching census geometries or aggregate bounds. |
| `met_zone_code` | Loads geometries or bounds for the metropolitan zone. |

The worker receives the same top-level field names, but their values are
canonical GeoJSON dictionaries:

```json
{
  "location": {
    "type": "FeatureCollection",
    "features": [
      {
        "id": "area-1",
        "type": "Feature",
        "geometry": {
          "type": "Polygon",
          "coordinates": [[
            [-99.20, 19.30],
            [-99.10, 19.30],
            [-99.10, 19.40],
            [-99.20, 19.40],
            [-99.20, 19.30]
          ]]
        },
        "properties": {}
      }
    ],
    "crs": { "type": "name", "properties": { "name": "EPSG:4326" } }
  },
  "year": 2025
}
```

Invalid wrappers return `422`. Database resolution failures return `503`.

## Runner Code

Decorated metric functions receive parsed SDK geometry models and can pass them
directly to `lyra-utils`.

```python
from lyra.sdk import LocationInput, RunContext
from lyra.sdk.models import TableJobResult
from lyra.utils.geometry import convert_geojson_to_gdf


def calculate(
    location: LocationInput,
    *,
    context: RunContext,
) -> TableJobResult:
    context.emit_event("progress", {"message": "Loading location"})
    context.check_cancelled()

    gdf = convert_geojson_to_gdf(location)

    values = {feature_id: 1 for feature_id in gdf.index}
    return TableJobResult.from_mapping(
        job_id=context.job_id,
        input_index=gdf.index,
        columns=["feature_value"],
        values={"feature_value": values},
    )
```

If your spatial computation already returns a Pandas or GeoPandas DataFrame,
use `TableJobResult.from_dataframe()` instead. If it returns one Pandas Series
indexed like the input `gdf`, use `TableJobResult.from_series()`.

Use `SingleGeoJSON` for fields declared with `kind: "bounds"`:

```json
{
  "name": "land_cover_raster",
  "description": "Generate a land cover raster for one area.",
  "entrypoint": "land_cover.runner:run",
  "inputs": {
    "bounds": { "kind": "bounds" },
    "year": { "kind": "integer", "minimum": 2020 }
  },
  "output": {
    "kind": "file",
    "media_type": "image/tiff",
    "extensions": [".tif", ".tiff"]
  }
}
```

## Sample Job

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "metric": "average_temperature_by_location",
    "input": {
      "location": {
        "data_type": "geojson",
        "value": {
          "type": "FeatureCollection",
          "features": [
            {
              "id": "area-1",
              "type": "Feature",
              "geometry": {
                "type": "Polygon",
                "coordinates": [[
                  [-99.20, 19.30],
                  [-99.10, 19.30],
                  [-99.10, 19.40],
                  [-99.20, 19.40],
                  [-99.20, 19.30]
                ]]
              },
              "properties": {}
            }
          ],
          "crs": {
            "type": "name",
            "properties": { "name": "EPSG:4326" }
          }
        }
      },
      "year": 2025
    }
  }'
```

A client could submit the same metric with:

```json
{
  "location": {
    "data_type": "cvegeo_list",
    "value": ["090020001", "090020002"]
  },
  "year": 2025
}
```
