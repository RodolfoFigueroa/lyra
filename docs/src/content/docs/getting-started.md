---
title: Getting Started
description: Configure Lyra with TOML and env vars, start Redis, run workers, and launch the API server.
---

Lyra reads server settings from `/lyra_data/config/lyra.toml`. Docker Compose
mounts that config and the Earth Engine service account as read-only files,
passes Postgres/admin settings as environment variables, and uses the shared
`lyra_data` volume for Lyra-owned runtime state such as plugin checkouts,
`/lyra_data/state/plugins.toml`, job cache files, and optional logs.

## Prerequisites

You will need:

- Python managed by `uv`.
- Redis for the Celery broker, result backend, and job/event store.
- A Google Earth Engine service account key saved as JSON.
- Postgres connection details and an admin API key available as environment
  variables.

## Configure

Create these files:

```text
lyra_data/config/lyra.toml
secrets/service-account.json
```

The repository includes a copyable starting file at `config.example.toml`.
Copy `.env.example` to `.env` so Compose knows where those host files live and
which runtime env vars to pass into Lyra:

```bash
mkdir -p lyra_data/config secrets
cp config.example.toml lyra_data/config/lyra.toml
cp .env.example .env
```

Use this as a starting point for `/lyra_data/config/lyra.toml`:

```toml
schema_version = 1

[api]
host = "0.0.0.0"
port = 5219

[redis]
url = "redis://lyra-redis-dev:6379/0"

[earth_engine]
project = "your-gee-project-id"
# service_account_file = "/lyra_data/secrets/service-account.json"

[logging]
level = "INFO"
# file = "/lyra_data/logs/lyra.log"

[job_store]
ttl_seconds = 600

[plugins]
default_queue = "interactive"
allowed_queues = ["interactive", "batch"]
# initial_repos = ["owner/plugin-repo@main"]
# catalog_dir = "/lyra_data/plugins/catalog"
# runner_base_dir = "/lyra_data/plugins/runners"

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

Set these environment variables before starting Lyra:

```bash
export LYRA_POSTGRES_HOST=postgres
export LYRA_POSTGRES_PORT=5432
export LYRA_POSTGRES_DB=lyra
export LYRA_POSTGRES_USER=lyra
export LYRA_POSTGRES_PASSWORD=change-me
export LYRA_ADMIN_API_KEY=change-me
```

Do not put API keys or database passwords in TOML. The Earth Engine service
account remains a file reference; the commented path field uses the
Docker-oriented default under `/lyra_data`.

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

All Lyra app containers mount `lyra_data:/lyra_data` plus read-only file mounts
for `lyra.toml` and the service-account JSON. Compose also passes the
`LYRA_POSTGRES_*` and `LYRA_ADMIN_API_KEY` variables from `.env`. The worker
commands pass `interactive` or `batch`; queues, concurrency, install
directories, and temp directories come from `[workers.<name>]`.

## Direct Processes

Start Redis locally:

```bash
docker run -d -p 6379:6379 redis:alpine
```

Start the API and wait for its health endpoint. The API initializes plugin state
and metric routes before it begins serving requests:

```bash
uv run python -m lyra_app.main
curl http://localhost:5219/ready
```

Then start a worker by name:

```bash
uv run python -m lyra_app.worker_launcher interactive
```

The API listens on the host and port configured in `[api]`.

## Plugins And Queues

On a new data volume, `plugins.initial_repos` seeds enabled plugin sources before
the API catalog is loaded. Lyra generates their repo IDs, validates all sources
and manifests, assigns missing routes to `plugins.default_queue`, and only then
creates `/lyra_data/state/plugins.toml`. Once that file exists, later
`initial_repos` edits are ignored and plugin sources are managed through the
admin API. Add a plugin source after the API is running:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/plugin-repo@main"}'
```

Supported sources include GitHub entries, explicit `file://` local git
repositories, and development `dir://` directory snapshots. Use `dir://` for a
local mock plugin when you want refreshes to include uncommitted edits.

Refresh the catalog, then restart workers when the response recommends it:

```bash
curl -X POST http://localhost:5219/admin/plugin-catalog/refresh \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"

curl -X POST 'http://localhost:5219/admin/workers/restart?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Missing metric routes are added to `/lyra_data/state/plugins.toml` with
`plugins.default_queue` and the repo id that exposed the metric. Review or
change routes through `/admin/plugin-routing`.

If a worker starts before a new metric has an assignment, refresh the API
catalog first and then restart the worker.

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
