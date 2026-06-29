---
title: Metrics Catalog
description: Read client-facing metric metadata and JSON Schemas from the API catalog.
---

The API catalog is built from v2 plugin manifests. It exposes the schema
metadata clients need without including worker queue names or Python
entrypoints.

The catalog can be empty when `LYRA_PLUGIN_REPOS` is empty or configured
repositories do not contain valid v2 manifests.

## List Metrics

`GET /metrics` returns a list of `MetricInfoV2` objects:

```json
[
  {
    "name": "metric_name",
    "description": "Compute a metric for the input area.",
    "request_schema": {
      "type": "object",
      "properties": {
        "location": {
          "oneOf": [
            { "$ref": "#/$defs/CVEGEOListWrapper" },
            { "$ref": "#/$defs/GeoJSONWrapper" },
            { "$ref": "#/$defs/MetZoneCodeWrapper" }
          ]
        },
        "data": { "type": "object" }
      },
      "required": ["location", "data"]
    },
    "output": {
      "kind": "table",
      "columns": [
        {
          "name": "value",
          "type": "number",
          "unit": "dimensionless",
          "description": "Computed value for each input feature.",
          "nullable": false
        }
      ],
      "batched_columns": []
    }
  }
]
```

Each item includes:

- `name`
- `description`
- `request_schema`
- `output`

`output.kind` is either `table` or `file`. Table outputs include ordered static
column metadata and may include `batched_columns`, which describe columns the
worker expands from a bounded input array for each job. File outputs include a
`media_type` and allowed `extensions`.

A table metric that batches over `sectors` can expose:

```json
{
  "kind": "table",
  "columns": [],
  "batched_columns": [
    {
      "source": "sectors",
      "name_template": "job_accessibility_{value}",
      "type": "number",
      "unit": "jobs",
      "description_template": "Job accessibility for sector {value}.",
      "nullable": false,
      "batching_reason": "Reuses the network graph and travel-time matrix across all sector queries."
    }
  ]
}
```

Clients should treat `batched_columns` as a declaration. Concrete result column
names are available after submitting a job, using the source array order from
the validated input.

## Fetch One Metric

`GET /metrics/{metric_name}` returns one `MetricInfoV2`. Missing metrics return
`404`.

## Payload Validation

`POST /jobs` validates `input` against the selected metric's effective
`request_schema` before dispatching work. Every metric includes at least one
required spatial wrapper field injected from its manifest `spatial_inputs`
declaration.

After validation, the API resolves spatial wrappers into canonical GeoJSON for
the worker. Clients should treat the `/metrics` schema as the source of truth
for request payloads.

Keep request schemas focused on the public client payload. Worker-only details
belong in the plugin manifest's internal `execution` and `entrypoint` fields,
not in `/metrics`.
