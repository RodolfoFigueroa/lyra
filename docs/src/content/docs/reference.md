---
title: Reference
description: Server config, commands, and public API paths.
---

## Server Config

Lyra reads `/lyra_data/config/lyra.toml` by default. The config owns these
runtime settings:

| Section | Purpose |
| --- | --- |
| `[api]` | Bind host/port, externally reachable `public_base_url`, and trusted `forwarded_allow_ips`. |
| `[redis]` | Redis URL used by Celery and the Redis clients. |
| `[database]` | Readiness and retry timing for PostgreSQL failures. |
| `[database.api]` | Async API pool, connection, statement, and recycle limits. |
| `[database.spatial]` | Bounded API GeoPandas pool and statement limits. |
| `[database.worker]` | Per-worker-process pool and statement limits. |
| `[earth_engine]` | Earth Engine project and service account file reference. |
| `[logging]` | Application log level and optional log file. |
| `[job_store]` | Job status, result, and event TTL. |
| `[agent_submission_limit]` | Shared REST/MCP fixed-window quota. |
| `[mcp]` | Official Streamable HTTP MCP enablement and mount path. |
| `[plugins]` | Plugin runtime paths, first-run repos, default queue, and allowed queues. |
| `[workers.<name>]` | Worker queues, concurrency, install directory, and temp directory. |

PostgreSQL settings plus agent and admin keys are environment variables:

| Variable | Purpose |
| --- | --- |
| `LYRA_POSTGRES_HOST` | PostgreSQL host. |
| `LYRA_POSTGRES_PORT` | PostgreSQL port. |
| `LYRA_POSTGRES_DB` | PostgreSQL database name. |
| `LYRA_POSTGRES_USER` | PostgreSQL user. |
| `LYRA_POSTGRES_PASSWORD` | PostgreSQL password. |
| `LYRA_AGENT_API_KEY` | Bearer token required by MCP and every `/jobs` route. |
| `LYRA_ADMIN_API_KEY` | Bearer token required by `/admin/*` routes. |

Do not put API keys or database passwords in TOML. The Earth Engine service
account remains a file reference through `service_account_file`; by default
Lyra reads `/lyra_data/secrets/service-account.json`.

Docker-oriented runtime paths also default under `/lyra_data`: plugin catalog
sources use `/lyra_data/plugins/catalog`, runner installs use
`/lyra_data/plugins/runners/<worker>`, and worker temp files use
`/lyra_data/cache/jobs/<worker>`.

`plugins.initial_repos` accepts plugin source strings for a new data volume. The
API validates and installs those sources before atomically creating
`/lyra_data/state/plugins.toml`. The setting is ignored after that file exists.
Lyra owns the state file, and operators make subsequent repository and routing
changes through the admin API.

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

Run the terminal operator console against the default local API:

```bash
uv run lyra-tui --host localhost:5219 --no-secure
```

Build docs locally:

```bash
npm run build --prefix docs
```

Preview docs locally:

```bash
npm run preview --prefix docs
```

## API Paths And Access

| Access | Routes | Credential |
| --- | --- | --- |
| Public | `/live`, `/ready`, `/data-types`, `/metrics`, `/metrics/{metric_name}`, `/lookups/met-zones` | None |
| Agent | `/jobs` lifecycle routes and configured MCP mount | `LYRA_AGENT_API_KEY` |
| Admin | Every `/admin/*` route | `LYRA_ADMIN_API_KEY` |

The following table lists the concrete HTTP routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/live` | Return dependency-free API process liveness. |
| `GET` | `/ready` | Return Redis and PostgreSQL readiness; unavailable dependencies produce HTTP `503`. |
| `GET` | `/data-types` | Return grouped `location` and `bounds` wrapper schemas for explicit spatial inputs. |
| `GET` | `/metrics` | Return the public catalog fingerprint and metric schema metadata. |
| `GET` | `/metrics/{metric_name}` | Fetch one metric schema metadata record. |
| `POST` | `/jobs` | Submit a metric job. |
| `GET` | `/jobs/{job_id}` | Fetch current job status. |
| `GET` | `/jobs/{job_id}/events` | Stream typed SSE job events. |
| `GET` | `/jobs/{job_id}/result` | Fetch terminal JSON result metadata. |
| `GET` | `/jobs/{job_id}/result/descriptor` | Fetch compact result descriptor metadata, preview, and raw access links. |
| `GET` | `/jobs/{job_id}/result/table.jsonl` | Stream a successful table result as JSONL. |
| `GET` | `/jobs/{job_id}/result/download` | Download terminal file result bytes. |
| `GET` | `/lookups/met-zones` | Look up a metropolitan zone code by name. |
| `GET` | `/admin/plugin-repos` | List configured plugin repositories. |
| `POST` | `/admin/plugin-repos` | Add a plugin repository to Lyra-owned state. |
| `PATCH` | `/admin/plugin-repos/{repo_id}` | Update a plugin repository. |
| `DELETE` | `/admin/plugin-repos/{repo_id}` | Remove a plugin repository and its metric queue assignments from state. |
| `POST` | `/admin/plugin-repos/{repo_id}/sync` | Sync one enabled plugin source. |
| `POST` | `/admin/plugin-catalog/refresh` | Refresh plugin catalog repos and report whether workers need restart. |
| `POST` | `/admin/workers/restart` | Restart worker pools explicitly. |
| `GET` | `/admin/status` | Return API, Redis, catalog, queue, worker, and job-store summary. |
| `GET` | `/admin/config-summary` | Return secret-free runtime configuration summary. |
| `GET` | `/admin/catalog` | Return loaded catalog metadata and plugin source summary. |
| `GET` | `/admin/workers` | Return configured and observed worker summaries. |
| `GET` | `/admin/workers/{worker_name}` | Return one configured or observed worker detail. |
| `GET` | `/admin/queues` | Return queue assignments, consumers, and unknown depth markers. |
| `GET` | `/admin/jobs` | List recent jobs by optional status or metric filters. |
| `POST` | `/admin/jobs/{job_id}/cancel` | Request cancellation for an active job. |
| `GET` | `/admin/plugin-routing` | List metric queue assignments. |
| `PUT` | `/admin/plugin-routing/{metric_name}` | Set a metric queue assignment. |
| `DELETE` | `/admin/plugin-routing/{metric_name}` | Delete a metric queue assignment. |

## Plugin Source Forms

| Form | Meaning |
| --- | --- |
| `owner/repo` | Clone a GitHub repository's default branch. |
| `owner/repo@branch-or-tag` | Clone a GitHub branch or tag. |
| `https://github.com/owner/repo` | Clone a GitHub repository with an explicit URL prefix. |
| `https://github.com/owner/repo@branch-or-tag` | Clone a GitHub branch or tag with an explicit URL prefix. |
| `file:///absolute/path/to/repo` | Clone a local git repository from committed state. |
| `dir:///absolute/path/to/plugin` | Copy a development directory snapshot, including uncommitted edits. |

Raw filesystem paths are not supported. `file://` and `dir://` sources do not
support branch or tag refs. `dir://` is intended for development and testing;
refresh the catalog, then restart workers so they reinstall copied snapshots.
