---
title: Plugin Quickstart
description: Create a typed Lyra metric and generate its schema v3 manifest.
---

A Lyra plugin is a regular Python package containing a `PluginDefinition`, one
or more decorated metric functions, and a generated root `lyra.plugin.json`.
Python is the source of truth; do not edit the manifest by hand.

## Minimal Repository

```text
example-lyra-plugin/
  pyproject.toml
  lyra.plugin.json
  example_plugin/
    __init__.py
    metrics.py
```

## pyproject.toml

```toml
[project]
name = "example-lyra-plugin"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["lyra-sdk"]

[tool.lyra]
plugin = "example_plugin.metrics:plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["example_plugin"]
```

The generated manifest uses `[project].name` and `[project].version` for plugin
identity. Versions must be static. `[tool.lyra].plugin` points to the one
`PluginDefinition` object workers import.

## metrics.py

```python
from typing import Annotated

from lyra.sdk import LocationInput, PluginDefinition, RunContext
from lyra.sdk.models import TableJobResult
from lyra.sdk.models.plugin_v3 import TableOutputColumnV3, TableOutputV3
from pydantic import Field

plugin = PluginDefinition()


@plugin.metric(
    name="example_metric",
    description="Return the submitted value for each input feature.",
    output=TableOutputV3(
        kind="table",
        columns=[
            TableOutputColumnV3(
                name="value",
                type="number",
                unit="dimensionless",
                description="Submitted numeric value.",
            )
        ],
    ),
)
def calculate(
    location: LocationInput,
    value: Annotated[
        float,
        Field(description="Value copied into each output row."),
    ],
    *,
    context: RunContext,
) -> TableJobResult:
    context.emit_event("progress", {"message": "Preparing result"})
    context.check_cancelled()
    return TableJobResult.from_mapping(
        job_id=context.job_id,
        input_index=[feature.id for feature in location.features],
        columns=["value"],
        values={"value": [value for _feature in location.features]},
    )
```

The decorator returns `calculate` unchanged, so unit tests call it like an
ordinary Python function with a `GeoJSON`, a float, and a fake `RunContext`.
The registry privately stores the adapter workers use to parse resolved job
payloads into those values.

## Generate And Check The Manifest

```bash
uv run lyra-plugin build-manifest
uv run lyra-plugin check-manifest
```

Commit the generated `lyra.plugin.json`. `check-manifest` does not modify files;
it prints a unified diff and exits nonzero when Python definitions, project
metadata, or the committed artifact differ.

The API reads this static file without importing plugin code. Workers import
the configured registry, compare its live compiled contract with the manifest,
and refuse to start if it is stale.

## Preflight

```bash
uv pip install --python "$(which python)" --dry-run .
uv pip install --python "$(which python)" -e .
uv run lyra-plugin check-manifest
uv run python -c "from example_plugin.metrics import plugin; print(plugin.metric_names)"
uv run pytest
```

## Connect The Plugin

Push the repository and add it through the admin API:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/example-lyra-plugin@main"}'
```

Refresh the catalog, restart the recommended workers, and inspect
`GET /metrics/example_metric`. Clients submit spatial wrappers; the decorated
function receives the resolved `GeoJSON` declared by `LocationInput`.
