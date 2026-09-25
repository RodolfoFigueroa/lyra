---
title: Plugin Authoring
description: Wrap existing Python calculations with typed parameters and native results.
---

A metric is a synchronous Python function with a parameter model, spatial inputs,
and a declared output. Keep existing computation code in its own functions or
library and add a small Lyra adapter. The runnable example is
[`examples/lyra-plugin`](https://github.com/RodolfoFigueroa/lyra/tree/main/examples/lyra-plugin).
It contains table, file, and progress metrics plus a generated manifest.

## Declare a metric

Ordinary inputs belong to one Pydantic model. `MetricParameters` sets
`extra="forbid"` and `validate_default=True`. Use native `Field` declarations for
descriptions, constraints, defaults, and examples. The decorator infers the model
from the `parameters` argument; do not repeat it in a decorator mapping.

This table adapter follows the runnable example, with an optional execution context
used for progress:

```python
import pandas as pd
from pydantic import Field

from lyra.sdk import (
    LocationInput,
    MetricParameters,
    RunContext,
    TableColumn,
    TableOutput,
    metric,
)


class Parameters(MetricParameters):
    value: int = Field(description="Value copied into each output row.")


@metric(
    name="smoke_table_metric",
    description="Return the submitted value for each input feature.",
    output=TableOutput(
        columns=[
            TableColumn(
                name="value",
                type="integer",
                unit="count",
                description="Submitted value.",
            ),
        ]
    ),
)
def run_table(
    location: LocationInput,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    context.report_progress(stage="table", current=1, total=1)
    feature_ids = [feature.id for feature in location.features]
    return pd.DataFrame(
        {"value": [parameters.value for _ in feature_ids]},
        index=feature_ids,
    )
```

An adapter can instead pass `parameters.value` to an existing calculation. Use
`lyra.utils.geometry.convert_geojson_to_gdf(location)` if that calculation expects
a GeoDataFrame. The underlying library does not need to accept Lyra objects.

## Handler conventions

Supported argument names are `parameters`, `location`, `bounds`, and `context`.
Their order does not matter: the worker calls them by keyword. Positional-or-keyword
and keyword-only arguments are accepted; positional-only arguments, variadics,
argument defaults, async handlers, and generators are rejected.

- `parameters` is a concrete Pydantic model. Omit this argument for a metric with
  no ordinary inputs. Put defaults on model fields, never on handler arguments.
- `location: LocationInput` receives a resolved `GeoJSON` feature collection.
- `bounds: BoundsInput` receives a resolved `SingleGeoJSON` geometry.
- `context: RunContext` is optional. Declare it only when using platform services.

Every metric needs at least one spatial argument. Table metrics require `location`;
a metric may also declare `bounds`. Spatial arguments cannot be nullable or have
defaults. Lyra owns their schemas and descriptions. Return annotations are optional;
the explicit output declaration governs runtime validation.

Metric names must match `^[a-z][a-z0-9_]*$` and cannot start with `lyra_`.
Parameter fields use normal Pydantic names without aliases.

## Reuse existing models

An existing `BaseModel` subclass is accepted when the root and every nested model
set `extra="forbid"` and `validate_default=True`. Inherited settings count. Adapt
an incompatible model with a small subclass and, where necessary, compatible
nested field types. Lyra never mutates or clones a model to change its behavior.

Root parameter fields require descriptions. Nested descriptions and examples are
optional. Supported values are strings, booleans, integers, finite floats, null,
homogeneous scalar `Literal` enums, lists, string-keyed dictionaries, unions, and
nested models. Typed dictionaries are intentionally open mappings.

Use ordinary lists with fixed output columns. There is no keyed batch protocol
or request-dependent column template. Independent parameter sweeps can be separate
jobs; a plugin may process a list internally and return its declared fixed columns.

The initial contract excludes `Any`, arbitrary classes, root/recursive/unresolved
generic models, sets, tuples, bytes, datetimes, decimals, non-string dictionary
keys, aliases, default factories, custom serializers, computed fields, and custom
JSON Schema hooks or structural overrides. Registration errors identify the metric
and field path. These limits keep discovery and execution contracts predictable.

## Defaults and validation

Omission and nullability are independent:

| Model field | May be omitted | Accepts null | Default |
| --- | --- | --- | --- |
| `value: int` | No | No | — |
| `value: int = 1` | Yes | No | `1` |
| `value: int \| None` | No | Yes | — |
| `value: int \| None = None` | Yes | Yes | null |
| `value: int \| None = 1` | Yes | Yes | `1` |

Defaults must be deterministic JSON values. Mutable literal defaults follow
Pydantic's per-instance copying behavior. Manifest generation validates defaults
and examples against their schemas; dynamic default factories are unsupported.

The API checks the submitted JSON Schema without coercion or default injection.
The worker constructs the parameter model, applies defaults, and runs Python
validators. A numeric string is not an integer; a JSON number such as `1.0` may
satisfy an integer schema and is parsed using normal Pydantic JSON semantics.

Python-only validators can reject schema-valid requests. Prefer model-level after
validators for cross-field checks and preserve declared value types. Such a request
can be accepted, then fail in the worker with `invalid_input` before the handler
runs. An invalid semantic default has the same outcome. A successful schema check
does not promise successful execution of every domain rule.

Submitted input is retained in provenance. Omitting a defaulted field and explicitly
supplying its default remain distinct captured provenance.

A known Pydantic schema-generation limitation affects literal dictionary defaults
containing a `$ref` key. Generation may fail with `PluginDefinitionError`; ordinary
dictionary parameters remain supported. Lyra does not rewrite these defaults.

## Submit spatial references and nested parameters

For the example table metric:

```json
{
  "metric": "smoke_table_metric",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"value": 7}
  }
}
```

A declared parameter model always requires the `parameters` object, including `{}`
when all fields have defaults. For a parameterless metric, omit the property:

```json
{
  "metric": "smoke_file_metric",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"}
  }
}
```

The API resolves spatial wrappers before execution. REST and Python clients support
both spatial arguments together. MCP discovers dual-spatial metrics but its run
helper supports exactly one spatial input; see the [MCP workflow](../../use/mcp/).

## Native outputs

Return a DataFrame for `TableOutput`. Its unique string index must exactly match
location feature IDs in order. Columns must match the declared source columns in
name and order. Lyra does not align rows, stringify identifiers, or guess columns.
Each column declares a scalar type, unit, description, and nullability. Integer
columns reject floats and booleans. Nullable cells accept `None` and floating NaN
as JSON null; infinity, pandas NA/NaT, and nested objects are rejected.

Static `FractionOfLocationArea` derivations remain supported. Return source columns;
Lyra appends derived ratios using API-calculated areas. Local normalization requires
explicit area metadata and does not query a database.

Return a `pathlib.Path` for `FileOutput`. Declare its media type and allowed file
extensions, write beneath `context.temp_dir`, and return the path. Relative paths
are resolved against that directory. Missing files, paths outside it, escaping
symlinks, and unsupported suffixes fail normalization. The example's
`smoke_file_metric` demonstrates this workflow.

Lyra attaches job IDs and constructs terminal transport models. A Series,
dictionary, or job-bearing terminal model is not an accepted plugin return.
API clients still receive `TableJobResult` or `FileJobResult`; those describe
transported results, not what an adapter returns.

## Register and test locally

Register functions explicitly in a synchronous, parameterless factory:

```python
from lyra.sdk import PluginDefinition
from .metrics import run_table, run_file, run_progress


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[run_table, run_file, run_progress])
```

Configure `[tool.lyra].factory = "smoke_plugin.plugin:create_plugin"` in
`pyproject.toml`. Declare directly imported dependencies there, including pandas
when returning DataFrames. The SDK itself does not import pandas.

Decorated functions remain ordinary Python callables. In a local test, construct
parameters and resolved geometry and pass a fake context only if the handler needs
one. Direct calls neither resolve spatial references nor inject context or normalize
results. Test your existing calculation independently as usual.

The same preparation and normalization used by workers are available locally:

```python
# In the example plugin environment, with resolved location and a fake context:
from smoke_plugin.metrics import run_table
from smoke_plugin.plugin import create_plugin

plugin = create_plugin()
parameters = plugin.prepare_parameters("smoke_table_metric", {"value": 7})
frame = run_table(parameters=parameters, location=location, context=context)
result = plugin.normalize_result(
    "smoke_table_metric",
    frame,
    job_id="local-test",
    location=location,
)
assert result.data == [[7] for _ in location.features]
```

`prepare_parameters` raises `MetricInputError` for invalid input. `normalize_result`
raises `MetricResultError` with metric and field/row/column context. For file outputs,
supply `temp_dir`; for area derivations, supply `location_areas_m2`. Neither helper
requires API, Redis, RQ, PostGIS, or Earth Engine connections.

## Runtime context

`RunContext` supplies database access, a logger, a job temporary directory
and best-effort progress reporting.

`context.report_progress(stage=..., current=..., total=..., unit=..., message=...)`
publishes optional snapshots. Current is finite and nonnegative; total, when supplied,
is finite and positive with current no greater than total. Estimates may decrease and
stages, totals, and units may change. Updates are coalesced using
`jobs.progress_min_interval_ms` (default 1000 ms). Persistence failures do not fail computation.
Plugins may remain silent for hours. Tests for database-using adapters should supply
a strict fake database client.

## Generate and inspect the manifest

Run in the plugin project:

```sh
uv run pytest
uv run lyra-plugin describe smoke_table_metric
uv run lyra-plugin describe smoke_table_metric --json
uv run lyra-plugin build-manifest
uv run lyra-plugin check-manifest
```

Commit the generated `lyra.plugin.json`; never edit it manually. Format 5 contains
plugin identity, the factory, metric identity, a complete Draft 2020-12 request
schema, spatial metadata, and static output declarations. Generation reads project
metadata and the live factory. There is one manifest representation and no semantic
input compilation in the API.

`describe` reads the canonical contract. `check-manifest` rejects a stale artifact.
The API reads manifests without importing plugin code; worker startup checks that
the live definition matches. See [Publish and debug](../publish-and-debug/) for
packaging and deployment.
