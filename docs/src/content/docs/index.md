---
title: Lyra Docs
description: Documentation for Lyra's v2 plugin runner, async job API, and warm-worker deployment model.
---

Lyra is a REST API for computing accessibility and land-use metrics for spatial
units in Mexico. It exposes metric schemas through a manifest catalog, accepts
work through the `/jobs` HTTP API, and executes metric code in warm
queue-specific Celery workers.

## Current Model

- API processes read v2 plugin manifests and validate job requests.
- Worker processes install plugin code and run the generic `lyra.run_metric` task.
- Redis stores queued, progress, terminal event, status, and result records.
- Public execution uses `/jobs`, `/jobs/{job_id}`, `/jobs/{job_id}/events`, and `/jobs/{job_id}/result`.

## Choose A Path

- Run Lyra locally: start with [Getting Started](getting-started/), then use
  [Local Development](local-development/) for repository workflows.
- Change Lyra itself: read [Contributor Guide](contributor-guide/),
  [Architecture](architecture/), and
  [Testing And Quality](testing-and-quality/).
- Build a plugin: start with [Plugin Quickstart](plugin-quickstart/), follow
  the [Plugin Author Checklist](plugin-author-checklist/), then read
  [Plugin Manifests](plugin-manifests/),
  [Spatial Plugin Inputs](spatial-plugin-inputs/), [Runner Plugins](runner-plugins/),
  [lyra-sdk](lyra-sdk/), and [lyra-utils](lyra-utils/).
- Call Lyra from another application: use [Job API](job-api/) and
  [Metrics Catalog](metrics-catalog/) for HTTP behavior, [Python Client](python-client/)
  for client workflows, and [lyra-api](lyra-api/) for package reference.
- Work as an AI agent: use [AI Agent Guide](ai-agent-guide/) as the stable crawl
  entrypoint.

## Live OpenAPI Docs

When the API server is running, FastAPI also exposes generated OpenAPI
references:

- Swagger UI: `http://localhost:5219/docs`
- ReDoc: `http://localhost:5219/redoc`
