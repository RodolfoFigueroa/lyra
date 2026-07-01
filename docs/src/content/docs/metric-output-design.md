---
title: Metric Output Design
description: Choose table, file, static column, batched column, job, and metric boundaries for Lyra plugins.
---

Metric output design starts with the shape a client should consume. Choose the
output kind and column strategy before writing the manifest, because the worker
validates every successful result against that declaration.

For field-level syntax, see [Plugin Manifests](../plugin-manifests/). For runner
return models, see [Runner Plugins](../runner-plugins/).

## Choose The Output Kind

Use `kind: "table"` when the metric returns one row per feature in the resolved
`location` input. Table cells must be scalar values with declared types, units,
descriptions, and nullability.

Use `kind: "file"` when the result is a job-level artifact rather than
per-feature scalar values. Good file outputs include rasters, rendered map
images, reports, archives, and exports that clients should download or inspect
outside the table result shape.

## Design Table Columns

Table outputs can declare:

- `columns`: static columns known when the manifest is written.
- `batched_columns`: generated column groups expanded from a bounded request
  array.
- Both: stable base columns followed by request-array-backed generated columns.

Prefer the simplest declaration that represents the result honestly. A table
should stay easy for clients to render, compare, and test.

## Static Columns

Static `columns` should be the default for table metrics. Use them when every
job returns the same logical values, such as:

- area and fraction columns.
- population count and density columns.
- accessibility score and rank columns.
- boolean flags or classification labels.

Static columns are easiest for clients to understand from `/metrics`, because
their names, units, descriptions, order, and nullability are known before a job
is submitted.

```json
{
  "output": {
    "kind": "table",
    "columns": [
      {
        "name": "area_m2",
        "type": "number",
        "unit": "m2",
        "description": "Urbanized area in square meters."
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

## Batched Columns

Use `batched_columns` when one job can reuse meaningful shared work across a
bounded list of related variants. The manifest declares a metric-local
`kind: "batch"` input, and the output declaration says how each submitted item
becomes one result column.

Good fits include:

- one network graph reused across several sector filters.
- one travel-time matrix reused across named destination categories.
- one raster load or preprocessing pass reused across bounded class filters.

Each source item has a stable `key`, a plugin-specific `value`, and an optional
display `label`. Lyra owns the `key` and `label` protocol fields: it uses `key`
for column names, uses `label` for descriptions when present, and leaves
`value` for plugin computation. The plugin author defines the schema for
`value`.

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

Lyra compiles `sector_filters` into a strict request array with `minItems: 1`,
`uniqueItems: true`, `additionalProperties: false`, and a canonical `key`
schema. Plugin authors do not configure the `key` pattern; they choose the
batch input name and the plugin-owned `value` schema.

For a request with keys `sectors_091_092` and `retail`, the runner must return
columns `job_accessibility_sectors_091_092` and `job_accessibility_retail` in
that source-array order. Do not derive public column names from free-form
`value` fields such as regex patterns.

## Static And Batched Together

Static and batched declarations can be combined when every job has stable base
values plus generated variants. For example, a job accessibility metric can
return a static `total_jobs` column and then one `job_accessibility_{key}` column
for each requested sector filter.

Use this shape when the base values are useful on their own and the generated
columns are homogeneous variants with the same type and unit.

## File Outputs

Use file outputs for results that are large, visual, spatially continuous, or
meant to be downloaded as artifacts. Examples include GeoTIFF rasters, map
images, zipped geospatial exports, model reports, and bundles with multiple
files.

```json
{
  "output": {
    "kind": "file",
    "media_type": "image/tiff",
    "extensions": [".tif", ".tiff"]
  }
}
```

Do not force file-like results into table JSON just to keep the API response
uniform. A table is best for per-feature scalar values; a file is best when the
artifact itself is the result.

## Separate Jobs Or Metrics

Use separate jobs for independent parameter sweeps where batching would only
make the table wider. For example, if year or season runs do not share expensive
preprocessing, separate jobs are clearer and easier to retry.

Use separate metrics when outputs have different meanings, units, audiences, or
runtime behavior. Static named columns can belong in one metric when the values
are part of the same result family; separate metrics are better when clients
would choose, schedule, or interpret them independently.

## Decision Table

| Situation | Prefer |
| --- | --- |
| Same per-feature values for every job | Static `columns` |
| Bounded related variants with shared computation | `batched_columns` |
| Stable base values plus generated variants | `columns` and `batched_columns` |
| Raster, image, report, archive, or large artifact | `kind: "file"` |
| Independent scenarios with no shared work | Separate jobs |
| Different semantics, units, audiences, or runtime behavior | Separate metrics or static named columns |
