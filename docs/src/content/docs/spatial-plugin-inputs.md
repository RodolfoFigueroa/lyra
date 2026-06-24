---
title: Spatial Plugin Inputs
description: Author spatial request schemas and parse GeoJSON payloads inside Lyra runner plugins.
---

Spatial plugins have two separate contracts:

1. The API validates the job `input` object against the metric's manifest
   `request_schema`.
2. The worker passes that validated object to the runner as `job.input`, a plain
   Python dictionary.

Lyra does not automatically convert `job.input` into SDK geometry models. Parse
spatial payloads in the runner before using `lyra-utils`.

```python
from lyra.sdk.models.geometry import GeoJSON
from lyra.utils.geometry import convert_geojson_to_gdf

geojson = GeoJSON.model_validate(job.input["geometries"])
gdf = convert_geojson_to_gdf(geojson)
```

Plugin packages should import public runtime contracts from `lyra-sdk` and
helpers from `lyra-utils`. Do not import from `lyra_app` inside plugin packages.

## Raw GeoJSON Input

Use raw GeoJSON when clients will submit the exact features the metric should
process.

```json
{
  "type": "object",
  "required": ["geometries"],
  "additionalProperties": false,
  "properties": {
    "geometries": { "$ref": "#/$defs/geoJSON" }
  },
  "$defs": {
    "crs": {
      "type": "object",
      "required": ["type", "properties"],
      "additionalProperties": false,
      "properties": {
        "type": { "const": "name" },
        "properties": {
          "type": "object",
          "required": ["name"],
          "additionalProperties": false,
          "properties": {
            "name": { "type": "string", "minLength": 1 }
          }
        }
      }
    },
    "point": {
      "type": "object",
      "required": ["type", "coordinates"],
      "additionalProperties": false,
      "properties": {
        "type": { "const": "Point" },
        "coordinates": {
          "type": "array",
          "items": { "type": "number" }
        }
      }
    },
    "polygon": {
      "type": "object",
      "required": ["type", "coordinates"],
      "additionalProperties": false,
      "properties": {
        "type": { "const": "Polygon" },
        "coordinates": {
          "type": "array",
          "items": {
            "type": "array",
            "items": {
              "type": "array",
              "items": { "type": "number" }
            }
          }
        }
      }
    },
    "multiPolygon": {
      "type": "object",
      "required": ["type", "coordinates"],
      "additionalProperties": false,
      "properties": {
        "type": { "const": "MultiPolygon" },
        "coordinates": {
          "type": "array",
          "items": {
            "type": "array",
            "items": {
              "type": "array",
              "items": {
                "type": "array",
                "items": { "type": "number" }
              }
            }
          }
        }
      }
    },
    "feature": {
      "type": "object",
      "required": ["id", "type", "geometry", "properties"],
      "additionalProperties": false,
      "properties": {
        "id": { "type": "string", "minLength": 1 },
        "type": { "const": "Feature" },
        "geometry": {
          "oneOf": [
            { "$ref": "#/$defs/point" },
            { "$ref": "#/$defs/polygon" },
            { "$ref": "#/$defs/multiPolygon" }
          ]
        },
        "properties": { "type": "object" }
      }
    },
    "geoJSON": {
      "type": "object",
      "required": ["type", "features", "crs"],
      "additionalProperties": false,
      "properties": {
        "type": { "const": "FeatureCollection" },
        "features": {
          "type": "array",
          "minItems": 1,
          "items": { "$ref": "#/$defs/feature" }
        },
        "crs": { "$ref": "#/$defs/crs" }
      }
    }
  }
}
```

The SDK `GeoJSON` model accepts FeatureCollections with one or more features.
Feature geometries may be `Point`, `Polygon`, or `MultiPolygon`. Every feature
must have a non-empty `id`, a `geometry`, and a `properties` object. The CRS
object must be shaped as:

```json
{ "type": "name", "properties": { "name": "EPSG:4326" } }
```

For a single enclosing geometry, use `SingleGeoJSON` in runner code and set the
schema's `features` array to `minItems: 1` and `maxItems: 1`. `SingleGeoJSON`
allows `Point` and `Polygon`, but not `MultiPolygon`.

## Wrapper-Style Input

Wrapper payloads let clients describe spatial input by a discriminator:

```json
{ "data_type": "geojson", "value": { "...": "FeatureCollection" } }
```

```json
{ "data_type": "cvegeo_list", "value": ["090020001", "090020002"] }
```

```json
{ "data_type": "met_zone_code", "value": "MET_ZONE_CODE" }
```

`GET /data_types` returns grouped wrapper schemas:

```json
{
  "location": [
    {
      "data_type": "geojson",
      "description": "A GeoDataFrame in GeoJSON format.",
      "wrapper_schema": {}
    }
  ],
  "bounds": [
    {
      "data_type": "geojson",
      "description": "A GeoDataFrame in GeoJSON format containing a single geometry. Does not support MultiPolygon or GeometryCollection.",
      "wrapper_schema": {}
    }
  ]
}
```

Use `location` schemas when a metric should process one or more selected
features. Use `bounds` schemas when a metric needs one enclosing area. The two
groups both contain a `geojson` data type, but the location schema validates
`GeoJSON` and the bounds schema validates `SingleGeoJSON`.

A compact manifest schema for a wrapper-style location field looks like this:

```json
{
  "type": "object",
  "required": ["location"],
  "additionalProperties": false,
  "properties": {
    "location": {
      "oneOf": [
        {
          "type": "object",
          "required": ["data_type", "value"],
          "additionalProperties": false,
          "properties": {
            "data_type": { "const": "cvegeo_list" },
            "value": {
              "type": "array",
              "minItems": 1,
              "items": { "type": "string" }
            }
          }
        },
        {
          "type": "object",
          "required": ["data_type", "value"],
          "additionalProperties": false,
          "properties": {
            "data_type": { "const": "geojson" },
            "value": { "$ref": "#/$defs/geoJSON" }
          }
        },
        {
          "type": "object",
          "required": ["data_type", "value"],
          "additionalProperties": false,
          "properties": {
            "data_type": { "const": "met_zone_code" },
            "value": { "type": "string", "minLength": 1 }
          }
        }
      ]
    }
  }
}
```

Add the raw GeoJSON `$defs` from the previous section when using the `geojson`
branch. For an exact current schema, fetch `/data_types` from the running
deployment and copy the appropriate `wrapper_schema`.

## Temperature Plugin Example

This example assumes you already have:

```python
def get_temperatures(geometries):
    ...
```

where `geometries` is a list of geometry objects and the function returns one
average temperature per input geometry.

Repository layout:

```text
temperature-lyra-plugin/
  pyproject.toml
  lyra.plugin.json
  temperature_plugin/
    __init__.py
    runner.py
    temperature.py
```

`pyproject.toml`:

```toml
[project]
name = "temperature-lyra-plugin"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "lyra-sdk",
  "lyra-utils",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["temperature_plugin"]
```

`lyra.plugin.json`:

```json
{
  "schema_version": 2,
  "plugin": {
    "name": "temperature-lyra-plugin",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "average_temperature_by_geometry",
      "description": "Return average temperature for each submitted geometry.",
      "request_schema": {
        "type": "object",
        "required": ["geometries"],
        "additionalProperties": false,
        "properties": {
          "geometries": { "$ref": "#/$defs/geoJSON" }
        },
        "$defs": {
          "geoJSON": {
            "type": "object",
            "required": ["type", "features", "crs"],
            "additionalProperties": false,
            "properties": {
              "type": { "const": "FeatureCollection" },
              "features": { "type": "array", "minItems": 1 },
              "crs": { "type": "object" }
            }
          }
        }
      },
      "result_schema": {
        "type": "object",
        "required": ["feature_ids", "temperatures"],
        "additionalProperties": false,
        "properties": {
          "feature_ids": {
            "type": "array",
            "items": { "type": "string" }
          },
          "temperatures": {
            "type": "array",
            "items": { "type": "number" }
          }
        }
      },
      "execution": {
        "queue": "interactive"
      },
      "entrypoint": "temperature_plugin.runner:run"
    }
  ]
}
```

The manifest example keeps the embedded GeoJSON schema short. For production,
use the fuller schema from this page or copy the exact `geojson` wrapper schema
from `/data_types`.

`temperature_plugin/runner.py`:

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import JobEnvelope, JobResult
from lyra.sdk.models.geometry import GeoJSON
from lyra.utils.geometry import convert_geojson_to_gdf

from temperature_plugin.temperature import get_temperatures


def run(job: JobEnvelope, context: RunContext) -> JobResult:
    context.emit_event("progress", {"message": "Loading geometries"})
    context.check_cancelled()

    geojson = GeoJSON.model_validate(job.input["geometries"])
    gdf = convert_geojson_to_gdf(geojson)
    temperatures = get_temperatures(list(gdf.geometry))

    if len(temperatures) != len(gdf):
        return JobResult(
            job_id=job.job_id,
            status="failed",
            error={
                "type": "plugin_error",
                "message": "Temperature result count did not match geometry count",
            },
        )

    return JobResult(
        job_id=job.job_id,
        status="succeeded",
        result={
            "feature_ids": [str(feature_id) for feature_id in gdf.index],
            "temperatures": temperatures,
        },
    )
```

Sample job:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "metric": "average_temperature_by_geometry",
    "input": {
      "geometries": {
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
    }
  }'
```

Successful result:

```json
{
  "job_id": "job-id",
  "status": "succeeded",
  "result": {
    "feature_ids": ["area-1"],
    "temperatures": [24.8]
  },
  "result_type": null,
  "file_path": null,
  "error": null
}
```

The API does not validate `result` against `result_schema`. Keep result checks
inside plugin tests when output shape matters.
