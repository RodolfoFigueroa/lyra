---
title: Metrics Catalog
description: Read client-facing metric metadata and JSON Schemas from the API catalog.
---

The API catalog is built from schema v3 plugin manifests. Plugin authors write
compact semantic `inputs`; Lyra compiles those inputs into the effective JSON
Schema that clients use for job submission.

The catalog can be empty when `[plugins].repos` is empty or configured
repositories do not contain valid schema v3 manifests.

## List Metrics

`GET /metrics` returns a `MetricCatalogResponse` with a public
`catalog_fingerprint` and a list of `MetricInfoV3` objects. The response also
sets the same fingerprint as the `ETag` header. This example shortens the
spatial wrapper definitions; fetch the live route for the full schema:

```json
{
  "catalog_fingerprint": "hex-sha256",
  "metrics": [
    {
      "name": "metric_name",
      "description": "Compute a metric for the input area.",
      "request_schema": {
        "type": "object",
        "required": ["location", "year"],
        "additionalProperties": false,
        "properties": {
          "location": {
            "oneOf": [
              { "$ref": "#/$defs/CVEGEOListWrapperV3" },
              { "$ref": "#/$defs/GeoJSONLocationWrapperV3" },
              { "$ref": "#/$defs/MetZoneCodeWrapperV3" }
            ]
          },
          "year": {
            "type": "integer",
            "minimum": 2020
          }
        },
        "$defs": {
          "CVEGEOListWrapperV3": { "...": "canonical wrapper definition" },
          "GeoJSONLocationWrapperV3": { "...": "canonical wrapper definition" },
          "MetZoneCodeWrapperV3": { "...": "canonical wrapper definition" }
        }
      },
      "spatial_inputs": {
        "location": "location"
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
}
```

The `catalog_fingerprint` changes when the public metric contract changes: a
metric is added or removed, or a metric's name, description, `request_schema`,
`spatial_inputs`, or `output` declaration changes. It does not represent
internal deployment details such as plugin repo ids, worker queues, entrypoints,
or job state.

Each metric item includes:

- `name`
- `description`
- `request_schema`
- `spatial_inputs`
- `output`

The `request_schema` is the compiled client contract. It includes Lyra-owned
spatial wrapper schemas, plugin-owned scalar inputs, batch item schemas, and
`additionalProperties: false`.

The `spatial_inputs` object maps request field names to Lyra spatial input
kinds, currently `location` or `bounds`. Agent integrations use this metadata to
find the field that should receive a supported spatial wrapper without
reverse-engineering the JSON Schema. For the v1 MCP bridge, agents should use
raw metropolitan zone codes through these fields.

The catalog does not expose worker routing fields or Python entrypoints.

Lyra also derives lexical search text from the public catalog fields. The
derived text includes the metric name, description, input field names, input
descriptions when present, output kind, output column names, column
descriptions, and units. Optional plugin-authored search metadata such as tags
or domains is intentionally deferred.

## Outputs

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

`GET /metrics/{metric_name}` returns one `MetricInfoV3`. Missing metrics return
`404`.

## Payload Validation

`POST /jobs` validates `input` against the selected metric's effective
`request_schema` before dispatching work. Every metric includes at least one
required spatial wrapper field compiled from its v3 `inputs`.

After validation, the API resolves spatial wrappers into canonical GeoJSON for
the worker. Clients should treat the `/metrics` schema as the source of truth
for request payloads.

Keep request payloads focused on public metric inputs. Worker-only details
belong in server configuration and plugin entrypoints, not in `/metrics`.
