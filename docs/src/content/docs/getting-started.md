---
title: Getting Started
description: Configure Lyra with TOML, start Redis, run workers, and launch the API server.
---

Lyra reads server settings from `/lyra_data/config/lyra.toml`. The same
`lyra_data` volume is mounted by the API and every worker, so plugin catalogs,
worker installs, job cache files, logs, and config all live under one durable
tree.

## Prerequisites

You will need:

- Python managed by `uv`.
- Redis for the Celery broker, result backend, and job/event store.
- A Google Earth Engine service account key saved as JSON.
- Secret files under `/lyra_data/secrets`.

## Configure

Create these files:

```text
/lyra_data/config/lyra.toml
/lyra_data/secrets/admin_api_key
/lyra_data/secrets/postgres_password
/lyra_data/secrets/service-account.json
```

The repository includes a copyable starting file at `lyra.toml.example`.

Use this as a starting point for `/lyra_data/config/lyra.toml`:

```toml
schema_version = 1

[api]
host = "0.0.0.0"
port = 5219

[redis]
url = "redis://lyra-redis-dev:6379/0"

[database]
host = "postgres"
port = 5432
name = "lyra"
user = "lyra"
# password_file = "/lyra_data/secrets/postgres_password"

[earth_engine]
project = "your-gee-project-id"
# service_account_file = "/lyra_data/secrets/service-account.json"

[admin]
# api_key_file = "/lyra_data/secrets/admin_api_key"

[logging]
level = "INFO"
# file = "/lyra_data/logs/lyra.log"

[job_store]
ttl_seconds = 600

[plugins]
repos = ["owner/plugin-repo@main"]
default_queue = "interactive"
allowed_queues = ["interactive", "batch"]
# catalog_dir = "/lyra_data/plugins/catalog"
# runner_base_dir = "/lyra_data/plugins/runners"

[plugins.metric_queues]

[workers.interactive]
queues = ["interactive"]
concurrency = 4

[workers.batch]
queues = ["batch"]
concurrency = 2
```

For direct local processes, use `redis://localhost:6379/0` instead of the
Compose Redis hostname. For the production Compose file, use
`redis://redis:6379/0`.

Secrets are file references only. Do not put API keys, database passwords, or
service account JSON inline in TOML.

The commented path fields use Docker-oriented defaults under `/lyra_data`.
Mount local secret files into `/lyra_data/secrets` or uncomment those fields if
your deployment uses different container paths.

## Install

Install the workspace dependencies:

```bash
uv sync
```

## Docker Compose

The development Compose file starts the API, Redis, and two named worker pools:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

All Lyra app containers mount only `lyra_data:/lyra_data`. The worker commands
pass `interactive` or `batch`; queues, concurrency, install directories, and
temp directories come from `[workers.<name>]`.

## Direct Processes

Start Redis locally:

```bash
docker run -d -p 6379:6379 redis:alpine
```

Start a worker by name:

```bash
uv run python -m lyra_app.worker_launcher interactive
```

Start the API:

```bash
uv run python -m lyra_app.main
```

The API listens on the host and port configured in `[api]`.

## Plugin Queues

Plugin manifests do not choose queues. Lyra assigns each metric in
`[plugins.metric_queues]`. During API catalog refresh, missing metrics are added
with `plugins.default_queue` and written back to `lyra.toml`.

If a worker starts before a new metric has an assignment, refresh the API
catalog first and restart the worker.

## Smoke Test

List the metrics exposed by the current plugin catalog:

```bash
curl http://localhost:5219/metrics
```

Choose one metric name from that response. Submit a job using an input payload
that matches that metric's `request_schema`:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{"metric":"METRIC_NAME","input":{"SPATIAL_FIELD":{"data_type":"cvegeo_list","value":["090020001"]}}}'
```

Every metric includes at least one required spatial wrapper field. After the job
is accepted, stream events and fetch the terminal result using the returned
`job_id`.
