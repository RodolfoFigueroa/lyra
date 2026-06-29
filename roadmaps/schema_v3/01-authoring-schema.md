# 01. Authoring Schema

Schema v3 is the manifest format plugin authors write in `lyra.plugin.json`.
Authors must describe metric inputs with a compact Lyra DSL. They must not write
the top-level JSON Schema request object directly.

## Manifest Shape

The top-level manifest must be a strict object with these fields:

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
      "description": "Return a value for each input feature.",
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
            "description": "Submitted numeric value."
          }
        ]
      }
    }
  ]
}
```

Rules:

- `schema_version` must be integer `3`.
- `plugin.name` and `plugin.version` must be non-empty strings.
- `metrics` must contain at least one metric.
- Metric names must be unique inside one manifest and across the loaded catalog.
- `queue` must be a non-empty queue name.
- `entrypoint` must be exactly `module:function`.
- Unknown fields must be rejected at every manifest level.

## Inputs

Each metric must define `inputs` as an object whose keys are top-level request
field names. Input field names must be non-empty strings.

Every metric must define at least one spatial input using `kind: "location"` or
`kind: "bounds"`. Table metrics must define an input named `location` with
`kind: "location"`.

Inputs default to required. Plugin-owned scalar, enum, and `json_schema` inputs
may set `required: false`. Spatial inputs and batch inputs must remain required.
`nullable` defaults to `false`; optional absence and explicit `null` are separate
states.

Common input metadata:

```json
{
  "kind": "integer",
  "description": "Dataset year.",
  "default": 2025,
  "examples": [2024, 2025],
  "required": false,
  "nullable": false,
  "minimum": 2020,
  "maximum": 2026
}
```

Common metadata rules:

- `description` compiles to the effective property schema description.
- `default` compiles to the effective property schema default and must validate
  against the compiled input schema.
- `examples` compiles to the effective property schema examples and each example
  must validate against the compiled input schema.
- `required` defaults to `true`.
- `nullable` defaults to `false`.
- `required: false` must not be used on `location`, `bounds`, or `batch` inputs.
- `nullable: true` must not be used on `location`, `bounds`, or `batch` inputs.
- `default` must not be used on `location`, `bounds`, or `batch` inputs.

## Input Kinds

### `location`

`location` is a Lyra-owned spatial input. Authors must not define the JSON Schema
wrapper for it.

```json
{
  "location": {
    "kind": "location",
    "description": "Areas to evaluate."
  }
}
```

Lyra compiles `location` into the canonical location wrapper schema for clients
and resolves it to GeoJSON before workers run.

`location` inputs must be required.

### `bounds`

`bounds` is a Lyra-owned spatial input for one enclosing geometry.

```json
{
  "bounds": {
    "kind": "bounds",
    "description": "Area to export."
  }
}
```

Lyra compiles `bounds` into the canonical bounds wrapper schema for clients and
resolves it to a single GeoJSON feature collection before workers run.

`bounds` inputs must be required.

### `string`

```json
{
  "name": {
    "kind": "string",
    "min_length": 1,
    "max_length": 80,
    "pattern": "^[A-Za-z0-9 _-]+$"
  }
}
```

`min_length`, `max_length`, and `pattern` are optional. They compile to
`minLength`, `maxLength`, and `pattern`.

### `number`

```json
{
  "resolution_m": {
    "kind": "number",
    "minimum": 1,
    "maximum": 100
  }
}
```

`minimum` and `maximum` are optional numeric bounds.

### `integer`

```json
{
  "year": {
    "kind": "integer",
    "minimum": 2020,
    "maximum": 2026
  }
}
```

`integer` follows the same bounds as `number`, but it compiles to JSON Schema
`type: "integer"`.

### `boolean`

```json
{
  "include_metadata": {
    "kind": "boolean",
    "default": false,
    "required": false
  }
}
```

`boolean` does not accept type-specific options.

### `enum`

```json
{
  "mode": {
    "kind": "enum",
    "values": ["walk", "bike", "drive"],
    "default": "walk"
  }
}
```

`values` must be a non-empty list of unique JSON scalar values. Scalars are
strings, numbers, integers, or booleans. `values` must not include `null`;
`nullable: true` must be used when explicit `null` is allowed.

### `batch`

`batch` defines a metric-local array argument that can generate dynamic output
columns. The field name is local to the metric. For example,
`destination_categories` is an input name, not global configuration.

```json
{
  "destination_categories": {
    "kind": "batch",
    "max_items": 12,
    "value": {
      "kind": "string",
      "min_length": 1,
      "max_length": 128
    },
    "label": true
  }
}
```

Rules:

- `max_items` is required and must be an integer greater than or equal to `1`.
- `value` is required and must be a plugin-owned input schema.
- `value` must not be `location`, `bounds`, or `batch`.
- `label` defaults to `false`.
- When `label` is `true`, clients may submit optional display labels.
- Batch inputs must be required.
- Every batch input must be referenced by at least one table
  `batched_columns.source`.
- Lyra owns batch item `key` and optional `label` schemas.
- Authors must not define batch item JSON Schema directly.

### `json_schema`

`json_schema` is the escape hatch for plugin-owned input fields that need JSON
Schema features not modeled by the compact DSL.

```json
{
  "advanced_filter": {
    "kind": "json_schema",
    "schema": {
      "type": "object",
      "required": ["field", "op", "value"],
      "properties": {
        "field": { "type": "string" },
        "op": { "enum": ["eq", "gt", "lt"] },
        "value": {}
      },
      "additionalProperties": false
    }
  }
}
```

Rules:

- `schema` must be a valid JSON Schema object.
- `json_schema` may be used only for plugin-owned input fields.
- `json_schema` must not be used to override spatial wrappers, batch `key`,
  batch `label`, or batch item structure.

## Outputs

### Static Table Columns

```json
{
  "output": {
    "kind": "table",
    "columns": [
      {
        "name": "area_m2",
        "type": "number",
        "unit": "m2",
        "description": "Area in square meters."
      }
    ]
  }
}
```

Rules:

- Table output must declare at least one `columns` item or one
  `batched_columns` item.
- Static column names must be unique.
- Column `type` must be `number`, `integer`, `string`, or `boolean`.
- `unit` and `description` are required.
- `nullable` defaults to `false`.

### Dynamic Table Columns

```json
{
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
```

Rules:

- `source` must reference an input whose `kind` is `batch`.
- `name` replaces the v2 `name_template` field.
- `description` replaces the v2 `description_template` field.
- `name` must contain `{key}` and must not contain any other template field.
- `description` may contain `{key}` and `{label}`.
- `description` must not contain `{value}` or any unsupported template field.
- `batching_reason` does not exist in schema v3.
- Expanded static and dynamic column names must be unique.

### File Output

```json
{
  "output": {
    "kind": "file",
    "media_type": "image/tiff",
    "extensions": [".tif", ".tiff"]
  }
}
```

Rules:

- `media_type` must be a non-empty string.
- `extensions` must be a non-empty list.
- Each extension must start with `.` and include a suffix.
- Extensions must be unique case-insensitively.

## Invalid Examples

### Batched Output Source Is Missing

```json
{
  "inputs": {
    "location": { "kind": "location" }
  },
  "output": {
    "kind": "table",
    "batched_columns": [
      {
        "source": "sector_filters",
        "name": "jobs_{key}",
        "type": "number",
        "unit": "jobs",
        "description": "Jobs for {label}."
      }
    ]
  }
}
```

This must fail because `sector_filters` is not defined in `inputs`.

### Batched Output Source Is Not A Batch Input

```json
{
  "inputs": {
    "location": { "kind": "location" },
    "sector_filters": { "kind": "string" }
  },
  "output": {
    "kind": "table",
    "batched_columns": [
      {
        "source": "sector_filters",
        "name": "jobs_{key}",
        "type": "number",
        "unit": "jobs",
        "description": "Jobs for {label}."
      }
    ]
  }
}
```

This must fail because `sector_filters` is a plugin argument but not a batch
argument.

### Duplicate Static Column Names

```json
{
  "output": {
    "kind": "table",
    "columns": [
      {
        "name": "score",
        "type": "number",
        "unit": "ratio",
        "description": "First score."
      },
      {
        "name": "score",
        "type": "number",
        "unit": "ratio",
        "description": "Second score."
      }
    ]
  }
}
```

This must fail because static column names must be unique.

### Unsupported Template Field

```json
{
  "source": "destination_categories",
  "name": "accessibility_{label}_{key}",
  "type": "number",
  "unit": "destinations",
  "description": "Accessible destinations for {value}."
}
```

This must fail because `name` may use only `{key}`, and `description` must not
use `{value}`.

### Missing Table Location Input

```json
{
  "inputs": {
    "bounds": { "kind": "bounds" }
  },
  "output": {
    "kind": "table",
    "columns": [
      {
        "name": "value",
        "type": "number",
        "unit": "dimensionless",
        "description": "Example value."
      }
    ]
  }
}
```

This must fail because table metrics must define `inputs.location` with
`kind: "location"`.
