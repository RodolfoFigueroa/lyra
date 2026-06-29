# Schema V3 Roadmap

Schema v3 replaces the verbose plugin manifest authoring format with a compact
Lyra-specific schema. Plugin authors must describe semantic `inputs` and output
contracts. Lyra compiles those declarations into the effective JSON Schema used
by `/metrics` and `POST /jobs`, plus the runtime metadata used by workers.

Schema v3 is replacement work. The implementation must not parse legacy v2
manifests, must not run dual manifest loaders, and must not include compatibility
shims.

## Implementation Phases

1. Define the authoring schema in
   [01-authoring-schema.md](./01-authoring-schema.md).
2. Define the compiler contract in
   [02-compiler-contract.md](./02-compiler-contract.md).
3. Implement SDK models and validation in
   [03-sdk-models-and-validation.md](./03-sdk-models-and-validation.md).
4. Integrate compiled manifests into the API and worker in
   [04-app-integration.md](./04-app-integration.md).
5. Tighten runtime validation and output handling in
   [05-runtime-validation-and-output.md](./05-runtime-validation-and-output.md).
6. Update public docs and verify acceptance criteria in
   [06-docs-and-acceptance.md](./06-docs-and-acceptance.md).

## Canonical Examples

### Static Table Metric

```json
{
  "schema_version": 3,
  "plugin": {
    "name": "urban-metrics",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "urbanized_area",
      "description": "Compute urbanized area statistics for each input feature.",
      "queue": "interactive",
      "entrypoint": "urban_metrics.runner:run_urbanized_area",
      "inputs": {
        "location": { "kind": "location" },
        "year": {
          "kind": "integer",
          "minimum": 2020,
          "maximum": 2026
        }
      },
      "output": {
        "kind": "table",
        "columns": [
          {
            "name": "area_m2",
            "type": "number",
            "unit": "m2",
            "description": "Urbanized area in square meters."
          },
          {
            "name": "area_frac",
            "type": "number",
            "unit": "ratio",
            "description": "Urbanized fraction of the input feature."
          }
        ]
      }
    }
  ]
}
```

### Dynamic Table Metric

`destination_categories` is a metric-local argument. It is not global
configuration.

```json
{
  "schema_version": 3,
  "plugin": {
    "name": "accessibility-metrics",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "accessibility_by_destination",
      "description": "Compute accessibility for requested destination categories.",
      "queue": "interactive",
      "entrypoint": "accessibility_metrics.runner:run_accessibility",
      "inputs": {
        "location": { "kind": "location" },
        "destination_categories": {
          "kind": "batch",
          "max_items": 12,
          "value": {
            "kind": "string",
            "min_length": 1,
            "max_length": 128
          },
          "label": true
        },
        "travel_minutes": {
          "kind": "integer",
          "minimum": 1,
          "maximum": 180
        }
      },
      "output": {
        "kind": "table",
        "batched_columns": [
          {
            "source": "destination_categories",
            "name": "accessibility_{key}",
            "type": "number",
            "unit": "destinations",
            "description": "Accessible destinations for {label}."
          }
        ]
      }
    }
  ]
}
```

### File Metric

```json
{
  "schema_version": 3,
  "plugin": {
    "name": "raster-metrics",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "land_cover_raster",
      "description": "Generate a land cover raster for the requested bounds.",
      "queue": "heavy",
      "entrypoint": "raster_metrics.runner:run_land_cover_raster",
      "inputs": {
        "bounds": { "kind": "bounds" },
        "year": {
          "kind": "integer",
          "minimum": 2020,
          "maximum": 2026
        },
        "resolution_m": {
          "kind": "number",
          "minimum": 1,
          "maximum": 100
        }
      },
      "output": {
        "kind": "file",
        "media_type": "image/tiff",
        "extensions": [".tif", ".tiff"]
      }
    }
  ]
}
```

## Source Of Truth

The files in this directory are the source of truth for schema v3
implementation. Public documentation must be updated from these decisions after
the implementation is complete.
