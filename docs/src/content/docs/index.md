---
title: Lyra Docs
description: Documentation for Lyra's v2 plugin runner, async job API, and warm-worker deployment model.
---

Lyra is a REST API for computing accessibility and land-use metrics for spatial units in Mexico. It exposes metric schemas through a manifest catalog, submits work through the `/jobs` HTTP API, and executes metric code in warm queue-specific Celery workers.

## Current Model

- API processes read v2 plugin manifests and validate job requests.
- Worker processes install plugin code and run the generic `lyra.run_metric` task.
- Redis stores queued, progress, terminal event, status, and result records.
- Public execution uses `/jobs`, `/jobs/{job_id}`, `/jobs/{job_id}/events`, and `/jobs/{job_id}/result`.

## Choose A Path

If you want to run Lyra, start with [Getting Started](getting-started/) and [Local Development](local-development/).

If you are changing Lyra itself, read [Contributor Guide](contributor-guide/), [Architecture](architecture/), and [Testing And Quality](testing-and-quality/).

If you are building a plugin, start with [Plugin Quickstart](plugin-quickstart/), then read [Plugin Manifests](plugin-manifests/), [Spatial Plugin Inputs](spatial-plugin-inputs/), [Runner Plugins](runner-plugins/), and the [lyra-sdk](lyra-sdk/) and [lyra-utils](lyra-utils/) package references.

If you are calling Lyra from another application, use [Job API](job-api/), [Metrics Catalog](metrics-catalog/), [Python Client](python-client/), and the [lyra-api](lyra-api/) package reference.

If you are an AI agent, use [AI Agent Guide](ai-agent-guide/) as the stable crawl entrypoint.

## Live OpenAPI Docs

When the API server is running, FastAPI also exposes generated OpenAPI references:

- Swagger UI: `http://localhost:5219/docs`
- ReDoc: `http://localhost:5219/redoc`
