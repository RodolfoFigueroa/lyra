---
title: Plugin Manifests
description: Define v2 plugin metadata, metric schemas, queues, and runner entrypoints.
---

Lyra reads plugin catalog metadata from `lyra.plugin.json` files. This page is
the field-by-field reference for the v2 manifest format.

Manifests are intentionally strict: extra fields are rejected, and JSON Schemas
are checked when the manifest is parsed.

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
      "output": {
        "kind": "table",
        "columns": [
          {
            "name": "value",
            "type": "number",
            "unit": "dimensionless",
            "description": "Example value for each input feature."
          }
        ]
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
- `output`: successful output declaration. Use `kind: "table"` for value metrics and `kind: "file"` for file-producing metrics.
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

For table outputs, workers validate that the returned table index exactly
matches the resolved `location` feature IDs and that columns match the manifest
declaration. If a table declares `batched_columns`, the worker first expands
those declarations from the validated job input, then validates the concrete
result columns. For file outputs, workers validate the file media type,
extension, existence, and that the artifact is inside `context.temp_dir`.

## Output Declarations

Table metrics return one row per geometry in the resolved `location` input:

```json
{
  "output": {
    "kind": "table",
    "columns": [
      {
        "name": "area_m2",
        "type": "number",
        "unit": "m2",
        "description": "Urbanized area in square meters.",
        "nullable": false
      },
      {
        "name": "area_frac",
        "type": "number",
        "unit": "ratio",
        "description": "Urbanized area fraction.",
        "nullable": false
      }
    ]
  }
}
```

Table column types are `number`, `integer`, `string`, and `boolean`. Column
names must be unique. `unit` and `description` are required; `nullable`
defaults to `false`.

Use `batched_columns` only when one job can reuse substantial work across a
bounded input array. For example, a job accessibility metric can build a
network graph and travel-time matrix once, then calculate one output column for
each requested economic-sector filter. Batched inputs are arrays of objects
with fixed fields:

- `key`: required stable column identity.
- `value`: required plugin-specific computation value, such as a regex.
- `label`: optional human-readable text. If omitted, Lyra uses `key`.

```json
{
  "request_schema": {
    "type": "object",
    "properties": {
      "location": {},
      "sector_filters": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["key", "value"],
          "properties": {
            "key": {
              "type": "string",
              "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
              "minLength": 1,
              "maxLength": 64
            },
            "value": {
              "type": "string",
              "minLength": 1,
              "maxLength": 128
            },
            "label": {
              "type": "string",
              "minLength": 1,
              "maxLength": 120
            }
          },
          "additionalProperties": false
        },
        "minItems": 1,
        "maxItems": 20,
        "uniqueItems": true
      }
    },
    "required": ["location", "sector_filters"]
  },
  "output": {
    "kind": "table",
    "columns": [],
    "batched_columns": [
      {
        "source": "sector_filters",
        "name_template": "job_accessibility_{key}",
        "type": "number",
        "unit": "jobs",
        "description_template": "Job accessibility for {label}.",
        "nullable": false,
        "batching_reason": "Reuses the network graph and travel-time matrix across all sector filters."
      }
    ]
  }
}
```

For input:

```json
{
  "sector_filters": [
    {
      "key": "sectors_091_092",
      "value": "^09[12].*",
      "label": "Sectors 091 and 092"
    },
    {
      "key": "retail",
      "value": "^46.*"
    }
  ]
}
```

the worker expects result columns `job_accessibility_sectors_091_092` and
`job_accessibility_retail`, in that order. The plugin uses each item's `value`
for computation, while Lyra uses `key` for column names and `label` for
descriptions. Do not derive public column names from free-form values such as
regex patterns. Avoid `batched_columns` for independent parameter sweeps, such
as a temperature metric where each year or season can be queried with separate
jobs without shared preprocessing.

File metrics produce one job-level artifact:

```json
{
  "output": {
    "kind": "file",
    "media_type": "image/tiff",
    "extensions": [".tif", ".tiff"]
  }
}
```

## Validation Rules

Lyra parses manifests with strict SDK models. Unknown top-level, plugin,
metric, or execution fields are rejected.

`request_schema` must be an object schema with `properties` and `required`.
Every `spatial_inputs` field must appear in both places, and spatial input
fields are always required.

Keep raw GeoJSON out of top-level request field schemas. Spatial request
fields are placeholders in the manifest; Lyra replaces them with canonical
wrapper schemas in `/metrics`.

Metric names must be unique inside a manifest and across all configured plugin
repositories. Use plugin-specific prefixes if separate repositories might expose
similar metric names.

Table metrics must declare a spatial input named `location` with value
`"location"`. File metrics still declare the spatial inputs they need, but
their successful result is served as a file artifact rather than table JSON.

## Entrypoints

Entrypoints must be exactly `module:function`.

The module must be dot-separated Python identifiers, and the function must be
one Python identifier:

```text
example_plugin.runner:run
```

The referenced module must be importable after the plugin package is installed
by each worker that selects the metric's queue. If a selected entrypoint cannot
be imported, that worker registry will not load.

## Queue Ownership

Queue names are deployment-owned. A manifest can use any queue name as long as
the deployment has a worker service with matching `LYRA_RUNNER_QUEUES` and
Celery `-Q` settings.

If `LYRA_RUNNER_QUEUES` is unset on a worker, that worker imports every
installed plugin metric. Queue-specific deployments should set it explicitly.

The checked-in Compose examples use `interactive` and `batch`, but those names
are not special to Lyra.
