---
title: Plugin Authoring
description: Design typed inputs, outputs, runtime behavior, and generated manifests.
---

Every metric is a synchronous decorated function with at least one spatial
input and a declared terminal output. Use public contracts from `lyra-sdk`; do
not import application internals.

Declare metrics with the standalone `@metric` decorator, then assemble them in
one explicit, synchronous factory:

```python
from lyra.sdk import PluginDefinition

from .metrics import job_accessibility


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[job_accessibility])
```

Configure that parameterless factory under `[tool.lyra].factory`. Lyra imports
only the configured module and never scans the package for metric modules.

## Inputs

Declare `LocationInput` when output rows correspond to selected features and
`BoundsInput` when the computation needs one enclosing geometry. Lyra owns their
descriptions and wrapper schemas, so do not add `Field` metadata to spatial
parameters.

Ordinary parameters may use:

- `str`, `float`, `int`, and `bool`;
- `Literal[...]` enums;
- nested Pydantic models and typed JSON containers;
- `list[BatchItem[T]]` for bounded repeated values.

Declare every plugin-owned parameter in the metric decorator's `inputs`
mapping. `Input` owns its description, examples, validation constraints, and
optional JSON Schema extensions. `BatchInput` adds the item limit and optional
labels while its nested `items=Input(...)` describes each item value:

```python
@metric(
    name="job_accessibility",
    description="Calculate accessibility to matching jobs.",
    inputs={
        "limit": Input(
            description="Maximum number of results.",
            ge=1,
        ),
        "patterns": BatchInput(
            max_items=20,
            allow_labels=True,
            items=Input(
                description="Regex matched against the SCIAN/NAICS code.",
                examples=[r"^31\d{4}$", r"^311\d{3}$"],
            ),
        ),
    },
    output=...,
)
def run(
    location: LocationInput,
    patterns: list[BatchItem[str]],
    limit: int = 100,
) -> TableJobResult:
    ...
```

Function annotations remain authoritative for Python types and nullability;
function defaults remain authoritative for omission and defaults. Do not put
root input metadata in `Annotated[..., Field(...)]`. Other `Annotated`
metadata, such as custom Pydantic validators, remains supported. Fields inside
nested Pydantic models may continue using `Field` normally. Spatial inputs are
omitted from `inputs` because Lyra owns their metadata. Batch containers remain
required protocol fields and cannot define defaults.

### Defaults, omission, and null

The function signature is authoritative for input defaults. A Python default
makes an ordinary input omittable and is recorded as its manifest default. Put
the default after the annotation; `Input` deliberately has no default field:

```python
inputs={
    "limit": Input(description="Maximum number of results.", ge=1),
}

def run(location: LocationInput, limit: int = 100) -> TableJobResult:
    ...
```

Omission and nullability are independent. A union with `None` (written as
`T | None` or `Optional[T]`) permits an explicit JSON `null`, but does not by
itself make the input omittable. To permit both omission and `null`, annotate
the value as nullable and give it a default:

```python
inputs={
    "threshold": Input(
        description="Threshold, or null to disable filtering.",
    ),
}

def run(location: LocationInput, threshold: float | None = None) -> TableJobResult:
    ...
```

The resulting contracts are:

| Function parameter | May be omitted | Accepts `null` | Default |
| --- | --- | --- | --- |
| `value: int` | No | No | — |
| `value: int = 1` | Yes | No | `1` |
| `value: int \| None` | No | Yes | — |
| `value: int \| None = None` | Yes | Yes | `null` |
| `value: int \| None = 1` | Yes | Yes | `1` |

An em dash means that no default exists; `null` is an actual default value.
Defaults and examples must satisfy the annotated type and constraints or
manifest generation fails.

## Inspect a definition

Use `plugin.describe("metric_name")` for structured inspection in Python. The
CLI renders the same information as a table for one metric or the whole plugin:

```bash
uv run lyra-plugin describe
uv run lyra-plugin describe job_accessibility
uv run lyra-plugin describe job_accessibility --json
```

Inspection includes the clean handler signature, required/default state,
constraints, descriptions, and the output summary. Registration errors include
the handler signature and identify missing, unknown, or Lyra-owned input
declarations.

Clients submit spatial wrapper objects such as `geojson`, `cvegeo_list`, and
`met_zone_code`. The API validates the compiled request schema and resolves
those wrappers before the metric function receives SDK geometry models.

## Outputs

Use `TableOutputV4` for one scalar row per resolved `location` feature. Static
columns are preferred when every job returns the same concepts. Each column has
a name, type, unit, description, and nullability.

Use batched columns only when bounded variants share expensive preprocessing.
Each request item has a stable `key`, plugin-owned `value`, and optional label;
Lyra expands `{key}` and `{label}` in the declared column contracts. The runner
must return the resulting columns in source-array order.

Use `FileOutputV4` for rasters, images, reports, archives, and other artifacts
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
database helper, durable progress events, and cooperative cancellation. Every
worker validates database connectivity before accepting jobs, so plugins may use
`context.db` directly without a `None` check. A later database outage becomes a
retryable `database_unavailable` job failure.

Call `context.check_cancelled()` around expensive stages. Expected domain
failures may return `FailedJobResult`; unexpected exceptions and invalid results
are normalized by the worker. Unit-test contexts must provide a fake or mocked
`LyraDB`; a strict fake that rejects unexpected calls is preferred for metrics
that do not use the database.

## Generated manifest

Manifest schema v4 contains plugin identity, metric identity, compact semantic
inputs, output declarations, and the plugin factory. Generation reads
`[project]` and `[tool.lyra].factory` from `pyproject.toml` plus the live
definition returned by the factory.

The compiler rejects extra fields, invalid defaults/examples, duplicate metric
names, missing spatial inputs, invalid table contracts, and stale artifacts.
Use the generated [Python reference](../../reference/generated/python/) for
exact SDK model fields.
