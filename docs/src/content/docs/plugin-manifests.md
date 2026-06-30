---
title: Plugin Manifests
description: Define schema v3 plugin metadata, semantic inputs, outputs, queues, and runner entrypoints.
---

Lyra reads plugin catalog metadata from `lyra.plugin.json` files. This page is
the field-by-field reference for the schema v3 manifest format.

In schema v3, plugin authors write semantic `inputs`. Lyra compiles those
inputs into the effective JSON Schema used by `POST /jobs` and exposed by
`/metrics`. That keeps manifests short while preserving a precise client-facing
validation contract.

Manifests are intentionally strict: extra fields are rejected, input defaults
and examples are checked against their compiled schemas, and metric names must
be unique.

For end-to-end publishing checks, see
[Plugin Author Checklist](../plugin-author-checklist/).

## Manifest Shape

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
      "description": "Compute an example metric for each input feature.",
      "queue": "interactive",
      "entrypoint": "example_plugin.runner:run",
      "inputs": {
        "location": { "kind": "location" },
        "year": {
          "kind": "integer",
          "minimum": 2020,
          "maximum": 2026,
          "default": 2026
        }
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
      }
    }
  ]
}
```

## Required Fields

Top-level fields:

- `schema_version`: must be integer `3`.
- `plugin.name`: non-empty plugin name.
- `plugin.version`: non-empty plugin version.
- `metrics`: non-empty list of metric definitions.

Metric fields:

- `name`: unique metric name within the manifest and across the loaded catalog.
- `description`: client-facing summary.
- `queue`: queue name used by the API to dispatch jobs and by workers to select metrics.
- `entrypoint`: Python `module:function` reference imported by worker processes.
- `inputs`: semantic request input declarations.
- `output`: successful output declaration. Use `kind: "table"` for per-feature value metrics and `kind: "file"` for file-producing metrics.

Every metric must declare at least one spatial input using `kind: "location"`
or `kind: "bounds"`. Table metrics must declare an input named `location` with
`kind: "location"`, because table rows are validated against the resolved
location feature IDs.

## Inputs

Each key in `inputs` becomes a top-level request field. Inputs default to
required. Plugin-owned scalar, enum, and `json_schema` inputs may set
`required: false`; spatial and batch inputs must remain required.

All inputs may include:

- `description`: human-readable input text copied into the compiled JSON Schema.
- `examples`: example values checked against the compiled input schema.

Plugin-owned inputs may also include:

- `default`: default value checked against the compiled input schema.
- `required: false`: omit this field from the compiled schema's root `required` list.
- `nullable: true`: allow explicit `null` values.

Spatial and batch inputs are Lyra-owned protocol fields. They must not define
`default`, `nullable: true`, or `required: false`.

### Spatial Inputs

Use `kind: "location"` when the metric runs once per client-selected feature.
Use `kind: "bounds"` when the metric needs one enclosing geometry.

```json
{
  "inputs": {
    "location": { "kind": "location" },
    "year": { "kind": "integer", "minimum": 2020 }
  }
}
```

Lyra owns the wrapper schemas for spatial inputs. In the authoring manifest the
field is small; in `/metrics` it appears as a complete JSON Schema accepting the
supported wrapper payloads, such as `geojson`, `cvegeo_list`, and
`met_zone_code`.

### Scalar Inputs

Use scalar inputs for ordinary plugin parameters:

```json
{
  "inputs": {
    "location": { "kind": "location" },
    "year": {
      "kind": "integer",
      "minimum": 2020,
      "maximum": 2026,
      "default": 2026
    },
    "scenario": {
      "kind": "enum",
      "values": ["baseline", "intervention"]
    },
    "include_details": {
      "kind": "boolean",
      "required": false,
      "default": false
    }
  }
}
```

Supported scalar kinds:

| Kind | Compiled JSON Schema |
| --- | --- |
| `string` | `type: "string"` plus optional `minLength`, `maxLength`, and `pattern`. |
| `number` | `type: "number"` plus optional `minimum` and `maximum`. |
| `integer` | `type: "integer"` plus optional `minimum` and `maximum`. |
| `boolean` | `type: "boolean"`. |
| `enum` | `enum` from the supplied scalar `values`. |

### Batch Inputs

Use `kind: "batch"` for a bounded metric-local list that can generate dynamic
table columns. Batch inputs are ordinary metric arguments, not global Lyra
configuration.

```json
{
  "inputs": {
    "location": { "kind": "location" },
    "sector_filters": {
      "kind": "batch",
      "max_items": 20,
      "label": true,
      "value": {
        "kind": "string",
        "min_length": 1,
        "max_length": 128
      }
    }
  },
  "output": {
    "kind": "table",
    "batched_columns": [
      {
        "source": "sector_filters",
        "name": "job_accessibility_{key}",
        "type": "number",
        "unit": "jobs",
        "description": "Job accessibility for {label}."
      }
    ]
  }
}
```

Lyra compiles a batch input into a strict request array. Each submitted item has
a Lyra-owned `key`, plugin-owned `value`, and optional `label` when the manifest
sets `label: true`:

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

The plugin uses each item's `value` for computation. Lyra uses `key` for column
names and `label` for descriptions, falling back to `key` when no label is
present. Batch keys must be unique in a job request.

### JSON Schema Escape Hatch

Use `kind: "json_schema"` only for plugin-owned input fields that need a JSON
Schema shape not covered by the lighter DSL:

```json
{
  "inputs": {
    "location": { "kind": "location" },
    "advanced_options": {
      "kind": "json_schema",
      "required": false,
      "schema": {
        "type": "object",
        "properties": {
          "mode": { "enum": ["fast", "accurate"] }
        },
        "additionalProperties": false
      }
    }
  }
}
```

This escape hatch belongs to plugin-owned request fields. It cannot replace
Lyra-owned spatial wrappers or the Lyra-owned `key` and optional `label` fields
inside batch items.

## Output Declarations

Choose the output shape with [Metric Output Design](../metric-output-design/).
This section documents the manifest fields and validation rules.

Table metrics return one row per geometry in the resolved `location` input.
They must declare at least one static `columns` entry or one `batched_columns`
entry:

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
        "description": "Urbanized area fraction."
      }
    ]
  }
}
```

Table column types are `number`, `integer`, `string`, and `boolean`. Column
names must be unique. `unit` and `description` are required; `nullable`
defaults to `false`.

Batched column groups declare columns generated from a batch input:

```json
{
  "output": {
    "kind": "table",
    "batched_columns": [
      {
        "source": "sector_filters",
        "name": "job_accessibility_{key}",
        "type": "number",
        "unit": "jobs",
        "description": "Job accessibility for {label}.",
        "nullable": false
      }
    ]
  }
}
```

Each `batched_columns` entry uses:

- `source`: required batch input used to produce columns.
- `name`: required column name template. It must contain `{key}` and may not contain other template fields.
- `type`, `unit`, `nullable`: shared metadata for every generated column. `nullable` defaults to `false`.
- `description`: required description template. It may contain `{key}` and `{label}`.

Static columns are expanded first, followed by batched groups in manifest order
and source-array order. Expanded column names must be unique.

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

File results are validated by media type, extension, file existence, and
containment under the job's temporary directory.

## Validation Rules

Lyra parses manifests with strict SDK models and compiles them before exposing
metrics. Unknown top-level, plugin, metric, input, and output fields are
rejected.

Metric names must be unique inside a manifest and across all configured plugin
repositories. Use plugin-specific prefixes if separate repositories might expose
similar metric names.

Batch outputs must reference an input whose `kind` is `batch`. Every batch
input must be referenced by at least one table `batched_columns` entry.

Input defaults and examples must validate against the compiled input schema.
For a spatial field, keep the manifest declaration semantic and let Lyra inject
the wrapper schema.

Table metrics must declare `inputs.location` as `kind: "location"`. File metrics
declare the spatial inputs they need, often `kind: "bounds"` for one enclosing
area.

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
