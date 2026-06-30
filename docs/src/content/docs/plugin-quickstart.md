---
title: Plugin Quickstart
description: Create a minimal installable Lyra runner plugin with one schema v3 metric.
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

Every metric declares at least one spatial input with `kind: "location"` or
`kind: "bounds"`. Authors write compact semantic `inputs`; Lyra compiles those
inputs into the effective JSON Schema exposed by `/metrics` and used by
`POST /jobs`.

For table, file, static column, and generated column design choices, read
[Metric Output Design](../metric-output-design/) before expanding this minimal
example into a production metric.

## lyra.plugin.json

```json
{
  "schema_version": 3,
  "plugin": {
    "name": "example-lyra-plugin",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "example_metric",
      "description": "Return the submitted value for each input feature.",
      "entrypoint": "example_plugin.runner:run",
      "inputs": {
        "location": { "kind": "location" },
        "value": {
          "kind": "number",
          "description": "Value copied into each output row."
        }
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

`location` is deliberately small in the manifest. Lyra owns the spatial wrapper
schema and injects it into the compiled request schema. Clients still submit a
wrapper object, as shown in the job example below.

## runner.py

```python
from lyra.sdk.context import RunContext
from lyra.sdk.models import JobEnvelope, TableJobResult
from lyra.sdk.models.geometry import GeoJSON


def run(job: JobEnvelope, context: RunContext) -> TableJobResult:
    context.emit_event("progress", {"message": "Preparing result"})
    context.check_cancelled()
    location = GeoJSON.model_validate(job.input["location"])
    return TableJobResult.from_mapping(
        job_id=job.job_id,
        input_index=[feature.id for feature in location.features],
        columns=["value"],
        values={"value": [job.input["value"] for _feature in location.features]},
    )
```

The worker receives `job.input["location"]` after the API has resolved the
client wrapper into canonical GeoJSON.

## Preflight Before Publishing

Run these quick checks from the plugin repository before pushing the branch or
tag that Lyra will load:

```bash
uv pip install --python "$(which python)" --dry-run .
uv pip install --python "$(which python)" -e .
uv run python -c "from example_plugin.runner import run; print(run)"
uv run python -c "import json; from pathlib import Path; from lyra.sdk.models import PluginManifestV3, compile_plugin_manifest; manifest = PluginManifestV3.model_validate(json.loads(Path('lyra.plugin.json').read_text())); compile_plugin_manifest(manifest); print('manifest ok')"
```

The worker uses the same install path: it checks compatibility, installs
compatible plugins editable, and imports entrypoints for matching queues. If a
check fails here, fix it before publishing the plugin branch. If a selected
entrypoint cannot be imported after install, the worker registry will not load
for that worker process.

## Connect The Plugin To Lyra

Push the plugin to GitHub and add it through the admin API:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/example-lyra-plugin@main"}'
```

Run an API process and a worker whose queue matches the server assignment:

```bash
uv run python -m lyra_app.worker_launcher interactive
```

Metric queues are assigned by Lyra-owned plugin state, not in
`lyra.plugin.json` or `lyra.toml`. Missing routes use `plugins.default_queue`
during catalog refresh. Use `/admin/plugin-routing` to inspect or change them.

Refresh the catalog after changing plugin code or manifests:

```bash
curl -X POST http://localhost:5219/admin/plugin-catalog/refresh \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Restart workers when the refresh response recommends it:

```bash
curl -X POST 'http://localhost:5219/admin/workers/restart?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Confirm the API exposes the metric and its compiled request schema:

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
