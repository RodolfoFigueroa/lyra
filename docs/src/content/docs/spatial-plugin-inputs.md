---
title: Spatial Plugin Inputs
description: Author mandatory spatial wrapper inputs for Lyra runner plugins.
---

Every Lyra metric has at least one spatial input. Clients always submit spatial
fields through a wrapper object:

```json
{ "data_type": "geojson", "value": { "...": "FeatureCollection" } }
```

```json
{ "data_type": "cvegeo_list", "value": ["090020001", "090020002"] }
```

```json
{ "data_type": "met_zone_code", "value": "MET_ZONE_CODE" }
```

Raw GeoJSON is valid only as the `value` of the `geojson` wrapper. Do not expose
a top-level raw GeoJSON field in a metric manifest.

## Manifest Contract

Each metric declares its spatial fields with `spatial_inputs`. Keys are
top-level request field names. Values are:

| Value | Runner receives |
| --- | --- |
| `location` | `GeoJSON` with one or more features. |
| `bounds` | `SingleGeoJSON` with one enclosing geometry. |

The same fields must exist in `request_schema.properties` and
`request_schema.required`. Lyra replaces those placeholder field schemas with
canonical wrapper schemas when it builds the catalog exposed by `/metrics`.

```json
{
  "name": "average_temperature_by_location",
  "description": "Return average temperature for each submitted location.",
  "spatial_inputs": {
    "location": "location"
  },
  "request_schema": {
    "type": "object",
    "required": ["location", "year"],
    "additionalProperties": false,
    "properties": {
      "location": {},
      "year": { "type": "integer", "minimum": 2020 }
    }
  },
  "execution": {
    "queue": "interactive"
  },
  "entrypoint": "temperature_plugin.runner:run"
}
```

Fetch `GET /metrics/{metric_name}` to see the complete effective schema clients
must submit. `GET /data_types` still exposes the individual wrapper schemas for
UI builders and client tooling.

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

Runner plugins parse the resolved GeoJSON field with SDK models before using
`lyra-utils`.

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import JobEnvelope, JobResult
from lyra.sdk.models.geometry import GeoJSON
from lyra.utils.geometry import convert_geojson_to_gdf


def run(job: JobEnvelope, context: RunContext) -> JobResult:
    context.emit_event("progress", {"message": "Loading location"})
    context.check_cancelled()

    geojson = GeoJSON.model_validate(job.input["location"])
    gdf = convert_geojson_to_gdf(geojson)

    return JobResult(
        job_id=job.job_id,
        status="succeeded",
        result={"feature_count": len(gdf)},
    )
```

Use `SingleGeoJSON` for fields declared as `"bounds"`.

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
