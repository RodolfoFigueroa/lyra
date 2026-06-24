---
title: Lyra Docs
description: Documentation for Lyra's v2 plugin runner, async job API, and warm-worker deployment model.
---

Lyra is a REST API for computing accessibility and land-use metrics for spatial units in Mexico. It exposes metrics through JSON Schema metadata, submits work as Redis-backed Celery jobs, and executes metric code in warm queue-specific worker pools.

These docs describe the current v2 execution model:

- API containers read plugin manifests, validate requests, and dispatch jobs.
- Worker containers install runner plugin code and execute the generic `lyra.run_metric` Celery task.
- Public execution goes through the `/jobs` REST and SSE API.

## First Steps

Start with [Getting Started](getting-started/) to run Lyra locally or with Docker Compose.

Use [Job API](job-api/) when building a client that submits work and follows progress.

Use [Plugin Manifests](plugin-manifests/) and [Runner Plugins](runner-plugins/) when adding a metric.

Use [Deployment](deployment/) when wiring queues, workers, plugin catalogs, and plugin install volumes.

## Live OpenAPI Docs

When the API server is running, FastAPI also exposes generated OpenAPI references:

- Swagger UI: `http://localhost:5219/docs`
- ReDoc: `http://localhost:5219/redoc`
