---
title: Plugin Quickstart
description: Create a minimal installable Lyra runner plugin with one v2 metric.
---

A Lyra plugin is a regular installable Python package with a root
`lyra.plugin.json` manifest and one or more importable runner entrypoints.

For a publish-and-debug checklist, see
[Plugin Author Checklist](../plugin-author-checklist/).

## Minimal Repository

```text
example-lyra-plugin/
  pyproject.toml
  lyra.plugin.json
  example_plugin/
    __init__.py
    runner.py
```

## pyproject.toml

```toml
[project]
name = "example-lyra-plugin"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "lyra-sdk",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["example_plugin"]
```

Workers check plugin install compatibility with `uv pip install --dry-run`,
then install compatible plugins editable into the worker environment.

For spatial helpers, date helpers, or Earth Engine reduction utilities, add
`lyra-utils` to the plugin dependencies and see the [lyra-utils package
reference](../lyra-utils/). Plugins that only need runner contracts can depend
on `lyra-sdk` alone.

Every metric declares at least one spatial input. Read
[Spatial Plugin Inputs](../spatial-plugin-inputs/) after this quickstart for the
full wrapper contract and conversion flow.

## lyra.plugin.json

```json
{
  "schema_version": 2,
  "plugin": {
    "name": "example-lyra-plugin",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "example_metric",
      "description": "Return the submitted value for each input feature.",
      "spatial_inputs": {
        "location": "location"
      },
      "request_schema": {
        "type": "object",
        "properties": {
          "location": {},
          "value": { "type": "number" }
        },
        "required": ["location", "value"],
        "additionalProperties": false
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
      },
      "execution": {
        "queue": "interactive"
      },
      "entrypoint": "example_plugin.runner:run"
    }
  ]
}
```

## runner.py

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import JobEnvelope, TableJobResult
from lyra.sdk.models.geometry import GeoJSON


def run(job: JobEnvelope, context: RunContext) -> TableJobResult:
    context.emit_event("progress", {"message": "Preparing result"})
    context.check_cancelled()
    location = GeoJSON.model_validate(job.input["location"])
    return TableJobResult(
        job_id=job.job_id,
        index=[feature.id for feature in location.features],
        columns=["value"],
        data=[[job.input["value"]] for _feature in location.features],
    )
```

## Preflight Before Publishing

Run these quick checks from the plugin repository before pushing the branch or
tag that Lyra will load:

```bash
uv pip install --python "$(which python)" --dry-run .
uv pip install --python "$(which python)" -e .
uv run python -c "from example_plugin.runner import run; print(run)"
uv run python -c "import json; from pathlib import Path; from lyra.sdk.models import PluginManifestV2; PluginManifestV2.model_validate(json.loads(Path('lyra.plugin.json').read_text())); print('manifest ok')"
```

The worker uses the same install path: it checks compatibility, installs
compatible plugins editable, and imports entrypoints for matching queues. If a
check fails here, fix it before publishing the plugin branch. If a selected
entrypoint cannot be imported after install, the worker registry will not load
for that worker process.

## Connect The Plugin To Lyra

Push the plugin to GitHub and add it to `LYRA_PLUGIN_REPOS`:

```text
LYRA_PLUGIN_REPOS=owner/example-lyra-plugin@main
```

Run an API process and a worker whose queue matches the manifest:

```bash
LYRA_RUNNER_QUEUES=interactive \
uv run celery -A lyra_app.worker.celery_app worker --loglevel=info -Q interactive
```

`LYRA_RUNNER_QUEUES` selects which manifest queues the worker imports. Keep it
aligned with Celery's `-Q` value. If it is unset, the worker imports every
installed plugin metric.

Refresh the catalog after changing plugin code or manifests:

```bash
curl -X POST 'http://localhost:5219/update-plugins?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Confirm the API exposes the metric and its effective wrapper schema:

```bash
curl http://localhost:5219/metrics/example_metric
```

Submit a minimal job:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "metric": "example_metric",
    "input": {
      "location": {
        "data_type": "geojson",
        "value": {
          "type": "FeatureCollection",
          "features": [
            {
              "id": "area-1",
              "type": "Feature",
              "geometry": {
                "type": "Polygon",
                "coordinates": [[
                  [-99.20, 19.30],
                  [-99.10, 19.30],
                  [-99.10, 19.40],
                  [-99.20, 19.40],
                  [-99.20, 19.30]
                ]]
              },
              "properties": {}
            }
          ],
          "crs": {
            "type": "name",
            "properties": { "name": "EPSG:4326" }
          }
        }
      },
      "value": 1
    }
  }'
```
