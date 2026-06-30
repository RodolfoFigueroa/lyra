---
title: Local Development
description: Run Lyra locally, configure plugins, and work with the docs site.
---

This page is for contributors working in the Lyra repository. For first-run
configuration, start with [Getting Started](../getting-started/).

## Workspace

Install the Python workspace before running tests, the API, or workers:

```bash
uv sync
```

The root project requires Python `>=3.11` and includes workspace packages under
`packages/*`.

## Local Data Tree

Lyra runtime state belongs under `/lyra_data`:

```text
/lyra_data/
  config/lyra.toml
  state/plugins.toml
  cache/jobs/
  plugins/catalog/
  plugins/runners/
  secrets/service-account.json
  logs/
```

The app creates non-secret runtime directories when it starts. Create the
Earth Engine service-account file yourself under `/lyra_data/secrets`. Postgres
settings and the admin API key come from environment variables.

A repo-local `lyra_data/` directory is ignored by git and can be used as a
staging tree when copying files into the Docker volume.

## Direct API And Worker

Use direct processes when you are iterating on application or worker code and
want fast restarts. Start Redis, make sure `/lyra_data/config/lyra.toml` points
at `redis://localhost:6379/0`, export the `LYRA_POSTGRES_*` and
`LYRA_ADMIN_API_KEY` variables, then start one named worker:

```bash
uv run python -m lyra_app.worker_launcher interactive
```

Start the API:

```bash
uv run python -m lyra_app.main
```

The API host and port come from `[api]`.

## Docker Compose

Use Compose when you want the same single-volume shape used by deployment:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

The development stack starts the API, Redis, and two worker pools for
`interactive` and `batch`. Every Lyra app container mounts `lyra_data:/lyra_data`
plus read-only file mounts for `lyra.toml` and the service-account JSON. Copy
`.env.example` to `.env`, point the mount variables at your local files, and
set the Postgres/admin values.

## Plugin Catalog During Development

Configure plugin repositories through the admin API:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/plugin-a@main"}'
```

Local `file://` entries are committed-code sync sources, not live-edit mounts:
commit plugin changes, then refresh the catalog. When using Docker Compose,
local plugin repositories must be reachable from the API and worker containers
at the same absolute path used in the admin API repo source.

The API syncs catalog repositories into `plugins.catalog_dir`. Workers sync and
install runner repositories under `plugins.runner_base_dir` or the selected
worker's `install_dir`.

Metric queues live in `/lyra_data/state/plugins.toml` and are managed through
`/admin/plugin-routing`. Each worker pool imports and consumes the queues listed
in its `[workers.<name>]` table.

Refresh the catalog and restart worker pools:

```bash
curl -X POST 'http://localhost:5219/admin/plugin-catalog/refresh?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Workers do not hot-reload plugin code in process. The refresh route reloads the
API catalog and asks worker pools to restart so they reinstall plugin code.

## Docs Site

Install docs dependencies from the lockfile:

```bash
npm ci --prefix docs
```

The docs build generates the Python API reference from package source using
Griffe. Regenerate it directly when you want to inspect the generated Markdown:

```bash
npm run generate:api --prefix docs
```

Preview the built site:

```bash
npm run build --prefix docs
npm run preview --prefix docs -- --host 127.0.0.1 --port 4321
```

The local docs URL is `http://127.0.0.1:4321/lyra/`.
