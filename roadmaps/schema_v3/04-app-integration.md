# 04. App Integration

The API and worker must load v3 manifests, compile them once, and use the
compiled contract everywhere runtime behavior needs schemas or metadata.

## Manifest Loading

`load_plugin_manifest()` must read `lyra.plugin.json`, parse `PluginManifestV3`,
and compile it.

```json
{
  "schema_version": 3,
  "plugin": {
    "name": "example-plugin",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "example_metric",
      "description": "Return submitted values.",
      "queue": "interactive",
      "entrypoint": "example_plugin.runner:run",
      "inputs": {
        "location": { "kind": "location" },
        "value": { "kind": "number" }
      },
      "output": {
        "kind": "table",
        "columns": [
          {
            "name": "value",
            "type": "number",
            "unit": "dimensionless",
            "description": "Submitted value."
          }
        ]
      }
    }
  ]
}
```

The loader must return a compiled manifest object that includes plugin metadata
and compiled metrics. App code must not inspect v3 authoring `inputs` after
loading.

## Catalog Refresh

Catalog refresh must build registry entries from compiled metrics:

```json
{
  "metric": {
    "name": "example_metric",
    "request_schema": { "type": "object" },
    "output": { "kind": "table" }
  },
  "queue": "interactive",
  "entrypoint": "example_plugin.runner:run",
  "spatial_inputs": { "location": "location" }
}
```

Rules:

- Metric names must remain unique across all loaded plugin repositories.
- Request validators must be built from compiled effective `request_schema`.
- Catalog fingerprints must be based on deterministic compiled manifest payloads.
- API catalog loading must not import plugin entrypoint code.

## `/metrics` Exposure

`/metrics` and `/metrics/{name}` must expose compiled effective JSON Schema.
They must not expose the compact authoring DSL.

Example response shape:

```json
{
  "name": "example_metric",
  "description": "Return submitted values.",
  "request_schema": {
    "type": "object",
    "required": ["location", "value"],
    "properties": {
      "location": { "...": "canonical location wrapper schema" },
      "value": { "type": "number" }
    },
    "additionalProperties": false
  },
  "output": {
    "kind": "table",
    "columns": [
      {
        "name": "value",
        "type": "number",
        "unit": "dimensionless",
        "description": "Submitted value.",
        "nullable": false
      }
    ],
    "batched_columns": []
  }
}
```

For batched columns, `/metrics` must use v3 public field names:

```json
{
  "kind": "table",
  "batched_columns": [
    {
      "source": "destination_categories",
      "name": "accessibility_{key}",
      "type": "number",
      "unit": "destinations",
      "description": "Accessible destinations for {label}.",
      "nullable": false
    }
  ]
}
```

## `POST /jobs`

Job submission must validate input against the compiled effective schema:

```json
{
  "metric": "accessibility_by_destination",
  "input": {
    "location": {
      "data_type": "geojson",
      "value": { "type": "FeatureCollection", "features": [] }
    },
    "destination_categories": [
      {
        "key": "retail",
        "value": "^46.*",
        "label": "Retail"
      }
    ],
    "travel_minutes": 45
  }
}
```

After JSON Schema validation, the API must run Lyra-specific validation such as
unique batch-key checks. Then it must resolve spatial wrappers using compiled
`spatial_inputs`.

The job envelope stored and sent to Celery must contain resolved spatial values
exactly as workers expect today:

```json
{
  "job_id": "job-1",
  "metric": "accessibility_by_destination",
  "input": {
    "location": {
      "type": "FeatureCollection",
      "features": []
    },
    "destination_categories": [
      {
        "key": "retail",
        "value": "^46.*",
        "label": "Retail"
      }
    ],
    "travel_minutes": 45
  }
}
```

## Worker Registry

Worker startup must load and compile the same v3 manifests as the API. Worker
registry entries must store:

```json
{
  "metric_name": "accessibility_by_destination",
  "queue": "interactive",
  "entrypoint": "accessibility_metrics.runner:run_accessibility",
  "output": { "kind": "table" }
}
```

Rules:

- Workers must import only selected metric entrypoints after plugin installation.
- Workers must validate plugin results against compiled output metadata.
- Workers must not need the authoring `inputs` DSL after registry loading.

## Integration Tests

Add app tests for:

- Catalog refresh with a v3 static table metric.
- Catalog refresh with a v3 dynamic table metric.
- Catalog refresh with a v3 file metric.
- `/metrics` exposes effective JSON Schema and v3 output field names.
- `POST /jobs` accepts valid input built from compiled schemas.
- `POST /jobs` rejects invalid scalar input with `422`.
- `POST /jobs` rejects duplicate batch keys with `422`.
- Worker registry imports a v3 metric entrypoint and stores compiled output.
- Worker validates table and file results against compiled output metadata.
