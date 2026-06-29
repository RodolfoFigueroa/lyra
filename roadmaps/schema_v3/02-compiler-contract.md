# 02. Compiler Contract

The schema v3 compiler turns the authoring manifest into the runtime/catalog
contract consumed by the API and worker. Plugin authors write compact `inputs`.
Lyra compiles those inputs into effective JSON Schema and runtime metadata.

The compiled contract is the public contract for clients. `/metrics` must expose
compiled effective JSON Schema, not the compact authoring DSL.

## Compiler Inputs And Outputs

Input:

```json
{
  "schema_version": 3,
  "metrics": [
    {
      "name": "example_metric",
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

Output per metric:

```json
{
  "name": "example_metric",
  "description": "Return a value for each input feature.",
  "queue": "interactive",
  "entrypoint": "example_plugin.runner:run",
  "spatial_inputs": {
    "location": "location"
  },
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
        "description": "Submitted numeric value.",
        "nullable": false
      }
    ],
    "batched_columns": []
  }
}
```

`request_schema` in the compiled contract must be the effective schema used by
clients and job validation.

## Root Request Schema

Lyra compiles every metric input object into a JSON Schema object:

```json
{
  "type": "object",
  "required": ["location", "year"],
  "properties": {
    "location": { "...": "canonical location wrapper schema" },
    "year": {
      "type": "integer",
      "minimum": 2020,
      "maximum": 2026
    }
  },
  "additionalProperties": false
}
```

Rules:

- The compiled root schema must always use `type: "object"`.
- The compiled root schema must always use `additionalProperties: false`.
- Inputs with `required` omitted or `required: true` must be included in the
  root `required` list.
- Plugin-owned inputs with `required: false` must not be included in the root
  `required` list. Spatial inputs and batch inputs must reject
  `required: false` before compilation.
- Input property order must preserve authoring `inputs` order.

## Common Metadata Compilation

Common metadata compiles onto the effective property schema:

```json
{
  "kind": "integer",
  "description": "Dataset year.",
  "default": 2025,
  "examples": [2024, 2025],
  "minimum": 2020,
  "maximum": 2026
}
```

compiles to:

```json
{
  "type": "integer",
  "description": "Dataset year.",
  "default": 2025,
  "examples": [2024, 2025],
  "minimum": 2020,
  "maximum": 2026
}
```

If `nullable: true` is present on a plugin-owned input, Lyra compiles the
property schema as accepting either the compiled non-null schema or `null`:

```json
{
  "anyOf": [
    { "type": "string" },
    { "type": "null" }
  ]
}
```

## Spatial Inputs

Spatial input kinds compile into both `spatial_inputs` and effective wrapper
schemas.

```json
{
  "inputs": {
    "location": { "kind": "location" },
    "bounds": { "kind": "bounds" }
  }
}
```

compiles to:

```json
{
  "spatial_inputs": {
    "location": "location",
    "bounds": "bounds"
  },
  "request_schema": {
    "type": "object",
    "required": ["location", "bounds"],
    "properties": {
      "location": { "...": "canonical location wrapper schema" },
      "bounds": { "...": "canonical bounds wrapper schema" }
    },
    "additionalProperties": false
  }
}
```

Lyra owns the spatial wrapper schemas. Authors must not supply raw GeoJSON
schemas for top-level spatial inputs.

## Scalar Input Compilation

Scalar inputs compile directly to JSON Schema property schemas.

| v3 input | Compiled JSON Schema |
| --- | --- |
| `{"kind": "string"}` | `{"type": "string"}` |
| `{"kind": "number"}` | `{"type": "number"}` |
| `{"kind": "integer"}` | `{"type": "integer"}` |
| `{"kind": "boolean"}` | `{"type": "boolean"}` |
| `{"kind": "enum", "values": ["a", "b"]}` | `{"enum": ["a", "b"]}` |

Snake-case v3 options must compile to JSON Schema camel-case names. For example,
`min_length` compiles to `minLength`.

## Batch Input Compilation

Given:

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

Lyra compiles:

```json
{
  "type": "array",
  "minItems": 1,
  "maxItems": 12,
  "uniqueItems": true,
  "items": {
    "type": "object",
    "required": ["key", "value"],
    "additionalProperties": false,
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
    }
  }
}
```

Rules:

- `minItems` must always compile to `1`.
- `maxItems` must come from `max_items`.
- `uniqueItems` must always compile to `true`.
- `key` must always compile to Lyra's canonical key schema.
- `value` must compile from the batch input's plugin-owned `value` schema.
- `label` must be present only when the authoring input sets `label: true`.
- `required` inside batch items must always be exactly `["key", "value"]`.
- `additionalProperties` inside batch items must always be `false`.

If `label` is omitted or `false`, clients must not submit `label`, and runtime
template expansion must use the `key` as the label fallback.

## `json_schema` Escape Hatch

`json_schema` compiles by validating and copying its `schema`.

```json
{
  "advanced_filter": {
    "kind": "json_schema",
    "schema": {
      "type": "object",
      "required": ["field", "op"],
      "properties": {
        "field": { "type": "string" },
        "op": { "enum": ["eq", "gt", "lt"] }
      },
      "additionalProperties": false
    }
  }
}
```

compiles to the same property schema, with common metadata applied at the
property root when present.

`json_schema` must be allowed only for plugin-owned inputs. The compiler must
reject attempts to use `json_schema` to replace Lyra-owned spatial wrappers,
batch item `key`, batch item `label`, or batch item object structure.

## Output Compilation

Static table columns compile by applying defaults:

```json
{
  "name": "score",
  "type": "number",
  "unit": "ratio",
  "description": "Accessibility score."
}
```

compiles to:

```json
{
  "name": "score",
  "type": "number",
  "unit": "ratio",
  "description": "Accessibility score.",
  "nullable": false
}
```

Batched output columns keep v3 field names in the public catalog:

```json
{
  "source": "destination_categories",
  "name": "accessibility_{key}",
  "type": "number",
  "unit": "destinations",
  "description": "Accessible destinations for {label}.",
  "nullable": false
}
```

The runtime may use internal names for implementation, but public `/metrics`
output must use `name` and `description`, not `name_template` or
`description_template`.

## Mapping Table

| v3 authoring field | Compiled runtime/catalog field |
| --- | --- |
| `schema_version: 3` | Manifest parser selection for v3 only |
| `plugin.name` | Plugin catalog metadata |
| `plugin.version` | Plugin catalog metadata |
| `metric.name` | Metric name and registry key |
| `metric.description` | Metric catalog description |
| `metric.queue` | Worker dispatch queue |
| `metric.entrypoint` | Worker import target |
| `metric.inputs` | Effective `request_schema` |
| `inputs.<field>.kind: "location"` | `spatial_inputs.<field> = "location"` and canonical wrapper schema |
| `inputs.<field>.kind: "bounds"` | `spatial_inputs.<field> = "bounds"` and canonical wrapper schema |
| `inputs.<field>.kind: "batch"` | Array schema with canonical `key`, `value`, optional `label` |
| `output.kind: "table"` | Table output validator contract |
| `output.columns` | Static table columns |
| `output.batched_columns` | Dynamic table columns expanded from batch inputs |
| `output.kind: "file"` | File output validator contract |

## Compiler Error Requirements

Compiler errors must mention v3 authoring paths. For example:

```text
metrics[0].output.batched_columns[0].source references 'sector_filters',
but inputs.sector_filters is not defined.
```

Errors must not require plugin authors to understand generated JSON Schema
internals unless the failing field is `kind: "json_schema"`.
