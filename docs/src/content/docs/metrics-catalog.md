---
title: Metrics Catalog
description: Read client-facing metric metadata and JSON Schemas from the API catalog.
---

The API catalog is built from v2 plugin manifests. It exposes client-facing schema metadata only; it does not expose worker queue names or Python entrypoints.

The catalog can be empty when `LYRA_PLUGIN_REPOS` is empty or configured repositories do not contain valid v2 manifests.

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
        "data": { "type": "object" }
      },
      "required": ["data"]
    },
    "result_schema": {
      "type": "object"
    }
  }
]
```

Each item includes:

- `name`
- `description`
- `request_schema`
- optional `result_schema`

## Fetch One Metric

`GET /metrics/{metric_name}` returns one `MetricInfoV2`. Missing metrics return `404`.

## Payload Validation

`POST /jobs` validates `input` against the selected metric's `request_schema` before dispatching work. The API uses the schema draft declared in the JSON Schema, when present.

Keep request schemas focused on the public client payload. Worker-only details belong in the plugin manifest's internal `execution` and `entrypoint` fields, not in `/metrics`.
