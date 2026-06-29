# 05. Runtime Validation And Output

Runtime validation must use the compiled schema v3 contract. JSON Schema handles
general request shape. Lyra-specific validators handle invariants that JSON
Schema does not express cleanly, especially batch key uniqueness.

## API Input Validation

`POST /jobs` must run validation in this order:

1. Confirm the metric exists.
2. Validate `input` against the compiled effective `request_schema`.
3. Validate Lyra-specific batch invariants.
4. Resolve spatial wrappers.
5. Store and dispatch the job envelope.

Example valid dynamic input:

```json
{
  "location": {
    "data_type": "geojson",
    "value": { "type": "FeatureCollection", "features": [] }
  },
  "destination_categories": [
    { "key": "retail", "value": "^46.*", "label": "Retail" },
    { "key": "education", "value": "^61.*", "label": "Education" }
  ],
  "travel_minutes": 45
}
```

JSON Schema validation must reject invalid structure, missing required fields,
unsupported extra fields, invalid key patterns, invalid labels, and invalid
plugin-owned values.

## Unique Batch Keys

JSON Schema `uniqueItems: true` does not guarantee unique `key` values when item
`value` differs. Lyra must validate unique keys for every compiled batch input.

Invalid input:

```json
{
  "destination_categories": [
    { "key": "retail", "value": "^46.*" },
    { "key": "retail", "value": "^47.*" }
  ]
}
```

The API must reject this before queueing the job with `422`.

Behavior-level error shape:

```json
{
  "loc": ["destination_categories"],
  "msg": "Batch input keys must be unique: retail.",
  "type": "unique_batch_keys"
}
```

If multiple batch fields have duplicate keys, the API must return one error per
field.

## Template Context

For each batch item, Lyra builds this template context:

```json
{
  "key": "retail",
  "label": "Retail"
}
```

If `label` was not submitted, Lyra must use the key as the label fallback:

```json
{
  "key": "retail",
  "label": "retail"
}
```

The template context must never include `value`.

## Table Output Expansion

For table metrics, workers must expand expected columns from compiled output
metadata and resolved job input.

Authoring output:

```json
{
  "kind": "table",
  "columns": [
    {
      "name": "total_jobs",
      "type": "integer",
      "unit": "jobs",
      "description": "Total jobs."
    }
  ],
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
```

Resolved input:

```json
{
  "destination_categories": [
    { "key": "retail", "value": "^46.*", "label": "Retail" },
    { "key": "education", "value": "^61.*" }
  ]
}
```

Expected result columns:

```json
[
  "total_jobs",
  "accessibility_retail",
  "accessibility_education"
]
```

Rules:

- Static columns expand first in manifest order.
- Batched column groups expand after static columns in manifest order.
- Batch source values expand in request array order.
- Expanded column names must be unique.
- Plugin results must return exactly the expected column list.
- Plugin results must return one row per resolved `location` feature ID.
- Table cell values must match compiled column type and nullability.

## Invalid Table Results

If a plugin returns columns that do not match the compiled contract, the worker
must persist a failed result with `error.type = "invalid_result"`.

Example invalid plugin result:

```json
{
  "kind": "table",
  "job_id": "job-1",
  "index": ["area-1"],
  "columns": ["accessibility_retail"],
  "data": [[1.5]]
}
```

If the compiled output expected `["total_jobs", "accessibility_retail"]`, the
worker must fail the job because the table result columns do not match the metric
output declaration.

## File Output Validation

File output validation remains tied to the compiled output metadata.

Compiled file output:

```json
{
  "kind": "file",
  "media_type": "image/tiff",
  "extensions": [".tif", ".tiff"]
}
```

Plugin result:

```json
{
  "kind": "file",
  "job_id": "job-1",
  "file_path": "result.tif",
  "media_type": "image/tiff"
}
```

Rules:

- Result `media_type` must match compiled `media_type`.
- Result file extension must match one compiled extension case-insensitively.
- Relative result paths must resolve under `context.temp_dir`.
- Absolute result paths must still be inside `context.temp_dir`.
- The file must exist and must be a file.
- Invalid file results must persist `error.type = "invalid_result"`.

## Runtime Failure Categories

API validation failures must return `422` and must not queue a job:

```json
{
  "detail": [
    {
      "loc": ["travel_minutes"],
      "msg": "180 is greater than the maximum of 120",
      "type": "maximum"
    }
  ]
}
```

Worker result validation failures must persist terminal failed job results:

```json
{
  "status": "failed",
  "error": {
    "type": "invalid_result",
    "message": "Table result columns must match the metric output declaration."
  }
}
```

Plugin-raised exceptions must continue to persist failed job results with the
existing execution-error behavior.

## Runtime Tests

Add tests for:

- Duplicate batch keys rejected by the API before queueing.
- Batch labels fall back to keys during description expansion.
- Batched output columns expand in source-array order.
- Static columns precede dynamic columns.
- Expanded column collisions fail as `invalid_result`.
- Invalid table index fails as `invalid_result`.
- Invalid table value type fails as `invalid_result`.
- File media type mismatch fails as `invalid_result`.
- File extension mismatch fails as `invalid_result`.
- File path outside `context.temp_dir` fails as `invalid_result`.
