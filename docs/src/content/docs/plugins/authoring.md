---
title: Plugin Authoring
description: Design typed inputs, outputs, runtime behavior, and generated manifests.
---

Every metric is a synchronous decorated function with at least one spatial
input and a declared terminal output. Use public contracts from `lyra-sdk`; do
not import application internals.

## Inputs

Declare `LocationInput` when output rows correspond to selected features and
`BoundsInput` when the computation needs one enclosing geometry. Lyra owns their
descriptions and wrapper schemas, so do not add `Field` metadata to spatial
parameters.

Ordinary parameters may use:

- `str`, `float`, `int`, and `bool`;
- `Literal[...]` enums;
- nested Pydantic models and typed JSON containers;
- `Annotated[list[BatchItem[T]], Batch(...)]` for bounded repeated values.

Use Python defaults for optional fields and `Annotated[..., Field(...)]` for
descriptions, examples, and constraints. Put batch value metadata on `T`, not
on the outer list. Spatial and batch containers remain required protocol fields.

Clients submit spatial wrapper objects such as `geojson`, `cvegeo_list`, and
`met_zone_code`. The API validates the compiled request schema and resolves
those wrappers before the metric function receives SDK geometry models.

## Outputs

Use `TableOutputV3` for one scalar row per resolved `location` feature. Static
columns are preferred when every job returns the same concepts. Each column has
a name, type, unit, description, and nullability.

Use batched columns only when bounded variants share expensive preprocessing.
Each request item has a stable `key`, plugin-owned `value`, and optional label;
Lyra expands `{key}` and `{label}` in the declared column contracts. The runner
must return the resulting columns in source-array order.

Use `FileOutputV3` for rasters, images, reports, archives, and other artifacts
that should be downloaded rather than represented as per-feature scalars.
Independent parameter sweeps should normally be separate jobs; outputs with
different meaning, units, audiences, or runtime behavior should be separate
metrics.

Return tables with the constructor matching the computation:

- `TableJobResult.from_mapping()` for mappings or aligned sequences;
- `from_dataframe()` for Pandas or GeoPandas tables;
- `from_series()` for one indexed series.

The result job ID must match `context.job_id`. Table indices must equal resolved
location feature IDs after string conversion, and columns must exactly match
the expanded output declaration. Write file artifacts below `context.temp_dir`
and return `FileJobResult`.

## Runtime context

`RunContext` provides the job and metric names, logger, temporary directory,
optional database helper, durable progress events, and cooperative cancellation.
Call `context.check_cancelled()` around expensive stages. Expected domain
failures may return `FailedJobResult`; unexpected exceptions and invalid results
are normalized by the worker.

## Generated manifest

Manifest schema v3 contains plugin identity, metric identity, compact semantic
inputs, output declarations, and the registry entrypoint. Generation reads
`[project]` and `[tool.lyra]` from `pyproject.toml` plus live decorated
definitions.

The compiler rejects extra fields, invalid defaults/examples, duplicate metric
names, missing spatial inputs, invalid table contracts, and stale artifacts.
Use the generated [Python reference](../../reference/generated/python/) for
exact SDK model fields.
