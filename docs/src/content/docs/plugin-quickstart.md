---
title: Plugin Quickstart
description: Create a minimal installable Lyra runner plugin with one v2 metric.
---

A Lyra plugin is an installable Python package with a root `lyra.plugin.json` manifest and one or more importable runner entrypoints.

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

Workers check plugin install compatibility with `uv pip install --dry-run` and then install compatible plugins editable into the worker environment.

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
      "description": "Return the submitted value and feature count.",
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
      "result_schema": {
        "type": "object",
        "properties": {
          "value": { "type": "number" },
          "feature_count": { "type": "integer" }
        },
        "required": ["value", "feature_count"],
        "additionalProperties": false
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
from lyra.sdk.models import JobEnvelope, JobResult
from lyra.sdk.models.geometry import GeoJSON


def run(job: JobEnvelope, context: RunContext) -> JobResult:
    context.emit_event("progress", {"message": "Preparing result"})
    context.check_cancelled()
    location = GeoJSON.model_validate(job.input["location"])
    return JobResult(
        job_id=job.job_id,
        status="succeeded",
        result={
            "value": job.input["value"],
            "feature_count": len(location.features),
        },
    )
```

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

Refresh the catalog after changing plugin code or manifests:

```bash
curl -X POST 'http://localhost:5219/update-plugins?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Then call `GET /metrics` and submit a job for `example_metric`.
