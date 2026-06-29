# 03. SDK Models And Validation

The SDK must expose schema v3 authoring models and compiler functions. The
models validate the compact manifest authors write. The compiler validates
cross-field relationships and produces the compiled runtime/catalog contract.

## Model Additions

Add strict v3 models for the authoring manifest:

```python
PluginManifestV3
PluginInfoV3
MetricManifestV3
InputSpecV3
TableOutputV3
BatchedColumnV3
FileOutputV3
```

The public parse entrypoint must parse only schema v3 manifests:

```python
manifest = PluginManifestV3.model_validate(raw_json)
compiled = compile_plugin_manifest(manifest)
```

The compiler must return compiled objects with these per-metric fields:

```json
{
  "name": "example_metric",
  "description": "Return a value for each input feature.",
  "queue": "interactive",
  "entrypoint": "example_plugin.runner:run",
  "spatial_inputs": { "location": "location" },
  "request_schema": { "type": "object" },
  "output": { "kind": "table" }
}
```

Existing v2 manifest models must be removed from the public SDK export and the
active loading path.

## Authoring Model Validation

Model validation must reject malformed local fields before compilation:

```json
{
  "schema_version": 3,
  "plugin": { "name": "", "version": "0.1.0" },
  "metrics": []
}
```

This must fail because `plugin.name` is empty and `metrics` is empty.

Required model rules:

- Unknown fields must be rejected.
- `schema_version` must be exactly `3`.
- Names, descriptions, queues, and entrypoints must be non-empty.
- Entrypoints must be `module:function`, where module parts and function name
  are Python identifiers.
- `metrics` must contain at least one metric.
- Metric names must be unique inside one manifest.
- Output column types must be `number`, `integer`, `string`, or `boolean`.
- File extensions must start with `.` and must be unique case-insensitively.

## Compiler Validation

Compiler validation must reject cross-field errors:

```json
{
  "inputs": {
    "location": { "kind": "location" },
    "category": { "kind": "string" }
  },
  "output": {
    "kind": "table",
    "batched_columns": [
      {
        "source": "category",
        "name": "value_{key}",
        "type": "number",
        "unit": "count",
        "description": "Value for {label}."
      }
    ]
  }
}
```

This must fail because `category` is not a batch input.

Required compiler rules:

- Every metric must contain at least one spatial input.
- Every table metric must contain `inputs.location` with `kind: "location"`.
- Every table output must define at least one static column or one batched
  column.
- Static column names must be unique.
- Every batch input must be referenced by at least one batched column source.
- Every batched column source must reference an input whose `kind` is `batch`.
- Batch `value` must not use `location`, `bounds`, or `batch`.
- Batch input `max_items` must be greater than or equal to `1`.
- Batched column `name` must contain `{key}` and must not contain any other
  template field.
- Batched column `description` may contain `{key}` and `{label}` only.
- `json_schema.schema` must be valid JSON Schema.
- Defaults and examples must validate against their compiled input schemas.

## Error Expectations

Errors must point at authoring paths:

```text
metrics[0].inputs.destination_categories.max_items must be >= 1
```

Errors must not expose generated paths such as:

```text
request_schema.properties.destination_categories.items.properties.key
```

The only exception is `kind: "json_schema"`, where errors may include JSON
Schema validator messages because the author supplied raw JSON Schema.

## Compiler Function Contract

The compiler function must be deterministic. Given the same v3 manifest, it must
produce the same compiled JSON-serializable payload.

```python
compiled = compile_plugin_manifest(manifest)
payload = compiled.model_dump(mode="json")
```

The compiled payload must be safe to use for:

- Catalog fingerprinting.
- `/metrics` responses.
- `POST /jobs` input validation.
- Worker registry entries.
- Worker output validation.

## Test Plan

Add SDK tests for valid manifests:

- Minimal static table metric.
- Table metric with optional scalar input.
- Table metric with dynamic columns from a batch input.
- Table metric with both static and dynamic columns.
- File metric with bounds input.
- Plugin-owned `json_schema` input.

Add SDK tests for invalid manifests:

- Unknown fields.
- Bad `schema_version`.
- Empty plugin name or version.
- Duplicate metric names.
- Invalid entrypoint format.
- Table metric missing `inputs.location`.
- Batch input not referenced by a batched column.
- Batched column source missing from inputs.
- Batched column source referencing a non-batch input.
- Unsupported template fields.
- Invalid file extension.
- Invalid raw JSON Schema in `json_schema.schema`.

Add compiler output tests:

- Root schema uses `type: "object"` and `additionalProperties: false`.
- Required inputs compile into root `required`.
- Optional inputs do not compile into root `required`.
- Spatial inputs compile into `spatial_inputs` and canonical wrapper schemas.
- Batch inputs compile canonical `key`, plugin-owned `value`, optional `label`,
  `minItems: 1`, `maxItems`, `uniqueItems: true`, and
  `additionalProperties: false`.
- Compiled request schemas pass `jsonschema` schema checks.
- Examples in `roadmaps/schema_v3/README.md` parse and compile.

## Public SDK Surface

The SDK must expose enough functionality for plugin authors and tests to validate
manifests locally:

```python
from lyra.sdk.models import PluginManifestV3, compile_plugin_manifest
```

The plugin author checklist must use this v3 API after implementation.
