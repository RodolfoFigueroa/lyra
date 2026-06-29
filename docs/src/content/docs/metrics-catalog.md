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

`output.kind` is either `table` or `file`. File outputs include a `media_type`
and allowed `extensions`.

Table outputs include ordered static `columns` and may include
`batched_columns`. Static columns are concrete result columns known from the
catalog before a job is submitted. Batched columns are declarations: the final
column names depend on the submitted source array and are available in the
terminal table result.

For `batched_columns`, clients should use the source array order from the
validated input. Each source item has a stable `key`, plugin-specific `value`,
and optional display `label`; Lyra uses `key` for column names and `label` for
descriptions. Plugin authors should use
[Metric Output Design](../metric-output-design/) when choosing an output shape.

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
