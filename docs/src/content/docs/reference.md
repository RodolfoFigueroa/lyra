---
title: Reference
description: Server config, commands, and public API paths.
---

## Server Config

Lyra reads `/lyra_data/config/lyra.toml` by default. The config owns these
runtime settings:

| Section | Purpose |
| --- | --- |
| `[api]` | API host and port. |
| `[redis]` | Redis URL used by Celery and the Redis clients. |
| `[database]` | PostgreSQL host, port, database, user, and password file reference. |
| `[earth_engine]` | Earth Engine project and service account file reference. |
| `[admin]` | Admin API-key file reference. |
| `[logging]` | Application log level and optional log file. |
| `[job_store]` | Job status, result, and event TTL. |
| `[plugins]` | Plugin repositories, catalog path, runner base path, default queue, and allowed queues. |
| `[plugins.metric_queues]` | Server-owned metric-to-queue assignments. |
| `[workers.<name>]` | Worker queues, concurrency, install directory, and temp directory. |

Secret values are not stored inline. Use `password_file`, `api_key_file`, and
`service_account_file` paths under `/lyra_data/secrets`. By default, Lyra reads
`/lyra_data/secrets/postgres_password`, `/lyra_data/secrets/admin_api_key`, and
`/lyra_data/secrets/service-account.json`.

Docker-oriented runtime paths also default under `/lyra_data`: plugin catalog
repos use `/lyra_data/plugins/catalog`, runner installs use
`/lyra_data/plugins/runners/<worker>`, and worker temp files use
`/lyra_data/cache/jobs/<worker>`.

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

Start a worker from TOML:

```bash
uv run python -m lyra_app.worker_launcher interactive
```

Start the API from TOML:

```bash
uv run python -m lyra_app.main
```

Build docs locally:

```bash
npm run build --prefix docs
```

Preview docs locally:

```bash
npm run preview --prefix docs
```

## Public API Paths

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/data_types` | Return grouped `location` and `bounds` wrapper schemas for explicit spatial inputs. |
| `GET` | `/metrics` | List metric schema metadata. |
| `GET` | `/metrics/{metric_name}` | Fetch one metric schema metadata record. |
| `POST` | `/jobs` | Submit a metric job. |
| `GET` | `/jobs/{job_id}` | Fetch current job status. |
| `GET` | `/jobs/{job_id}/events` | Stream typed SSE job events. |
| `GET` | `/jobs/{job_id}/result` | Fetch a terminal JSON result or file. |
| `GET` | `/met_zone_code` | Look up a metropolitan zone code by name. |
| `POST` | `/update-plugins` | Refresh plugin catalog repos and restart worker pools. |
