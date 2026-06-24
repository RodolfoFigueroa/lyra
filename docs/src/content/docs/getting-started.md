---
title: Getting Started
description: Install Lyra, run Redis, start a worker, and launch the API server.
---

This page gets a development instance running. Use Docker Compose for the smoothest path because it mounts the Earth Engine service account file where the application expects it.

## Prerequisites

Lyra expects:

- Python managed by `uv`.
- Redis for the Celery broker, result backend, and job/event store.
- A Google Earth Engine service account key saved as JSON.
- A `.env` file in the project root.

```text
EARTHENGINE_PROJECT=your-gee-project-id
SERVICE_ACCOUNT_BIND_PATH=C:\path\to\service-account.json
LYRA_CACHE_BIND_PATH=C:\path\to\lyra-cache
CELERY_BROKER_URL=redis://localhost:6379/0
LYRA_PLUGIN_REPOS=owner/plugin-repo@branch
LYRA_ADMIN_API_KEY=local-admin-secret
```

Optional logging settings:

```text
LYRA_LOG_LEVEL=INFO
LYRA_LOG_FILE=logs/lyra.log
```

The application initializes Earth Engine from `/app/service-account.json`. Docker Compose creates that path from `SERVICE_ACCOUNT_BIND_PATH`. Direct local runs must make the same absolute path available.

## Install

Install the workspace dependencies:

```bash
uv sync
```

## Run Redis

Start Redis locally:

```bash
docker run -d -p 6379:6379 redis:alpine
```

Set the broker URL if you are not using the Docker Compose defaults:

```bash
CELERY_BROKER_URL=redis://localhost:6379/0
```

## Configure Plugins

Lyra only lists metrics from configured plugin repositories. `LYRA_PLUGIN_REPOS` is a comma-separated list of GitHub repository entries:

```text
LYRA_PLUGIN_REPOS=owner/plugin-a,owner/plugin-b@main,https://github.com/owner/plugin-c@v0.1.0
```

Each plugin repository must contain `lyra.plugin.json` at its root. If `GET /metrics` returns an empty list, configure at least one plugin repo and refresh the catalog.

## Start A Worker

Workers consume deployment-owned queues. This example starts one worker pool for the `interactive` queue:

```bash
LYRA_RUNNER_QUEUES=interactive \
uv run celery -A lyra_app.worker.celery_app worker --loglevel=info -Q interactive
```

`LYRA_RUNNER_QUEUES` controls which v2 manifest metrics the worker imports. Celery's `-Q` value controls which queue messages the worker receives. Keep them aligned.

## Start The API

Run the API server:

```bash
uv run python -m lyra_app.main
```

The API listens on `http://localhost:5219` by default.

Use `LYRA_PORT` to choose another port.

## Docker Compose

The development Compose file starts the API, Redis, and warm worker pools:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

The checked-in Compose shape uses two example queues, `interactive` and `batch`. They are examples owned by the deployment. Plugin manifests choose a queue with each metric's `execution.queue` field.

## Smoke Test

List the metrics exposed by the current plugin catalog:

```bash
curl http://localhost:5219/metrics
```

Choose one metric name from that response. Submit a job using an input payload that matches that metric's `request_schema`:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{"metric":"METRIC_NAME","input":{}}'
```

Then stream events and fetch the terminal result using the returned `job_id`.
