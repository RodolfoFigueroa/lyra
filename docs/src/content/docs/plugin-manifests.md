---
title: Plugin Manifests
description: Define v2 plugin metadata, metric schemas, queues, and runner entrypoints.
---

Lyra reads plugin catalog metadata from `lyra.plugin.json` files. The API loads v2 manifests only.

The manifest is strict: extra fields are rejected. JSON Schemas are checked when the manifest is parsed.

## Manifest Shape

```json
{
  "schema_version": 2,
  "plugin": {
    "name": "example-plugin",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "example_metric",
      "description": "Compute an example metric for the input area.",
      "request_schema": {
        "type": "object",
        "properties": {
          "data": { "type": "object" }
        },
        "required": ["data"]
      },
      "result_schema": {
        "type": "object"
      },
      "execution": {
        "queue": "interactive"
      },
      "entrypoint": "example_plugin.runner:run"
    }
  ]
}
```

## Required Fields

Top-level fields:

- `schema_version`: must be integer `2`.
- `plugin.name`: non-empty plugin name.
- `plugin.version`: non-empty plugin version.
- `metrics`: non-empty list of metric definitions.

Metric fields:

- `name`: unique metric name within the manifest and across the loaded catalog.
- `description`: client-facing summary.
- `request_schema`: JSON Schema used to validate `/jobs` input.
- `result_schema`: optional JSON Schema describing successful result shape. Lyra checks that the schema itself is valid and exposes it through `/metrics`.
- `execution.queue`: queue name used by the API to dispatch jobs and by workers to select metrics.
- `entrypoint`: Python `module:function` reference imported by worker processes.

## Entrypoints

Entrypoints must be exactly `module:function`.

The module must be dot-separated Python identifiers, and the function must be one Python identifier:

```text
example_plugin.runner:run
```

The referenced module must be importable after the plugin package is installed by the worker.

## Queue Ownership

Queue names are deployment-owned. A manifest can use any queue name as long as the deployment has a worker service with matching `LYRA_RUNNER_QUEUES` and Celery `-Q` settings.

The checked-in Compose examples use `interactive` and `batch`, but those names are not special to Lyra.
