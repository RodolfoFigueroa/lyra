---
title: Reference
description: Environment variables, commands, and public API paths.
---

## Environment Variables

Core application settings:

| Variable | Purpose |
| --- | --- |
| `EARTHENGINE_PROJECT` | Google Earth Engine project ID. |
| `SERVICE_ACCOUNT_BIND_PATH` | Host path for the Earth Engine service account JSON in Compose. |
| `CELERY_BROKER_URL` | Redis URL used by Celery and the Redis clients. |
| `LYRA_PLUGIN_REPOS` | Plugin repository list used by plugin sync/install paths. |
| `LYRA_PLUGIN_CATALOG_DIR` | Directory containing API catalog manifests. |
| `LYRA_PLUGIN_INSTALL_DIR` | Directory where workers install plugin code. |
| `LYRA_RUNNER_QUEUES` | Comma-separated queue names a worker should import and execute. |
| `LYRA_RUNNER_TEMP_DIR` | Optional base directory for runner temporary job files. |
| `LYRA_CACHE_DIR` | Cache directory used as a fallback for runner temp files. |
| `LYRA_JOB_STORE_TTL_SECONDS` | TTL for job status, result, and event keys. Defaults to `600`. |
| `LYRA_ADMIN_API_KEY` | Required by admin routes such as plugin update. |
| `LYRA_LOG_LEVEL` | Application log level. |
| `LYRA_LOG_FILE` | Optional log file path. |

Database settings from `.env.example`:

| Variable | Purpose |
| --- | --- |
| `POSTGRES_USER` | PostgreSQL username. |
| `POSTGRES_PASSWORD` | PostgreSQL password. |
| `POSTGRES_DB` | PostgreSQL database name. |
| `POSTGRES_HOST` | PostgreSQL host. |
| `POSTGRES_PORT` | PostgreSQL port. |

## Local Commands

Install Python dependencies:

```bash
uv sync
```

Run tests:

```bash
uv run pytest
```

Run type checks:

```bash
uv run ty check
```

Run ruff:

```bash
uv run ruff check
```

Build docs locally:

```bash
npm run build --prefix docs
```

Preview docs locally:

```bash
npm run dev --prefix docs
```

## Public API Paths

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/data_types` | List accepted input data types. |
| `GET` | `/metrics` | List metric schema metadata. |
| `GET` | `/metrics/{metric_name}` | Fetch one metric schema metadata record. |
| `POST` | `/jobs` | Submit a metric job. |
| `GET` | `/jobs/{job_id}` | Fetch current job status. |
| `GET` | `/jobs/{job_id}/events` | Stream typed SSE job events. |
| `GET` | `/jobs/{job_id}/result` | Fetch a terminal JSON result or file. |
| `GET` | `/met_zone_code` | Look up a metropolitan zone code by name. |
