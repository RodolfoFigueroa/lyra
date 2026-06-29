---
title: Plugin Manifests
description: Define v2 plugin metadata, metric schemas, queues, and runner entrypoints.
---

Lyra reads plugin catalog metadata from `lyra.plugin.json` files. The API loads v2 manifests only.

The manifest is strict: extra fields are rejected. JSON Schemas are checked when the manifest is parsed.

For end-to-end publishing checks, see
[Plugin Author Checklist](../plugin-author-checklist/).

## Manifest Shape

```json
{
  "schema_version": 2,
  "plugin": {
    "name": "example-plugin",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "example_metric",
      "description": "Compute an example metric for the input area.",
      "spatial_inputs": {
        "location": "location"
      },
      "request_schema": {
        "type": "object",
        "required": ["location", "data"],
        "properties": {
          "location": {},
          "data": { "type": "object" }
        },
        "additionalProperties": false
      },
      "result_schema": {
        "type": "object"
      },
      "execution": {
        "queue": "interactive"
      },
      "entrypoint": "example_plugin.runner:run"
    }
  ]
}
```

## Required Fields

Top-level fields:

- `schema_version`: must be integer `2`.
- `plugin.name`: non-empty plugin name.
- `plugin.version`: non-empty plugin version.
- `metrics`: non-empty list of metric definitions.

Metric fields:

- `name`: unique metric name within the manifest and across the loaded catalog.
- `description`: client-facing summary.
- `spatial_inputs`: non-empty mapping of required top-level input fields to `location` or `bounds`.
- `request_schema`: JSON Schema used to validate `/jobs` input.
- `result_schema`: optional JSON Schema describing successful result shape. Lyra checks that the schema itself is valid and exposes it through `/metrics`.
- `execution.queue`: queue name used by the API to dispatch jobs and by workers to select metrics.
- `entrypoint`: Python `module:function` reference imported by worker processes.

Every metric must declare at least one spatial input. Each `spatial_inputs` key
must appear in `request_schema.properties` and `request_schema.required`. Lyra
replaces those placeholder field schemas with canonical wrapper schemas in the
catalog exposed by `/metrics`.

`POST /jobs` validates input against that effective schema. Before dispatch,
Lyra resolves spatial wrappers into canonical GeoJSON dictionaries in
`job.input`. Use the examples in
[Spatial Plugin Inputs](../spatial-plugin-inputs/) for complete request and
runner shapes.

`result_schema` is client-facing metadata. Lyra validates that the schema is
well formed, but the worker does not validate successful plugin output against
it at runtime.

## Validation Rules

Lyra parses manifests with strict SDK models. Unknown top-level, plugin,
metric, or execution fields are rejected.

`request_schema` must be an object schema with `properties` and `required`.
Every `spatial_inputs` field must appear in both places, and spatial input
fields are always required.

Do not define a raw GeoJSON or `FeatureCollection` schema for a top-level
request field. Spatial request fields are placeholders in the manifest; Lyra
replaces them with canonical wrapper schemas in `/metrics`.

If `request_schema.$defs` already contains a definition name used by a
canonical spatial wrapper, it must be identical to Lyra's definition. Conflicts
are rejected when the catalog is built.

Metric names must be unique inside a manifest and across all configured plugin
repositories. Use plugin-specific prefixes if separate repositories might expose
similar metric names.

## Entrypoints

Entrypoints must be exactly `module:function`.

The module must be dot-separated Python identifiers, and the function must be one Python identifier:

```text
example_plugin.runner:run
```

The referenced module must be importable after the plugin package is installed by the worker.

## Queue Ownership

Queue names are deployment-owned. A manifest can use any queue name as long as the deployment has a worker service with matching `LYRA_RUNNER_QUEUES` and Celery `-Q` settings.

The checked-in Compose examples use `interactive` and `batch`, but those names are not special to Lyra.
