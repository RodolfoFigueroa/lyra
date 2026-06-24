---
title: Getting Started
description: Install Lyra, run Redis, start a worker, and launch the API server.
---

## Prerequisites

Lyra expects:

- Python managed by `uv`.
- Redis for the Celery broker, result backend, and job/event store.
- A Google Earth Engine service account key saved as a JSON file.
- A `.env` file in the project root with at least `EARTHENGINE_PROJECT`.

```text
EARTHENGINE_PROJECT=your-gee-project-id
```

Optional logging settings:

```text
LYRA_LOG_LEVEL=INFO
LYRA_LOG_FILE=logs/lyra.log
```

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

Submit a job:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{"metric":"tree_coverage","input":{"data":{"data_type":"met_zone_code","value":"19.1.01"}}}'
```

Then stream events and fetch the terminal result using the returned `job_id`.
