---
title: Local Development
description: Run Lyra locally, configure plugins, and work with the docs site.
---

## Python Environment

Install the workspace:

```bash
uv sync
```

The root project requires Python `>=3.11` and includes the workspace packages under `packages/*`.

## Environment File

Create `.env` in the repository root. Start from `.env.example` and fill in the
values needed for your run mode.

Minimum values for most local work:

```text
EARTHENGINE_PROJECT=your-gee-project-id
SERVICE_ACCOUNT_BIND_PATH=C:\path\to\service-account.json
LYRA_CACHE_BIND_PATH=C:\path\to\lyra-cache
CELERY_BROKER_URL=redis://localhost:6379/0
LYRA_PLUGIN_REPOS=owner/plugin-repo@branch
LYRA_ADMIN_API_KEY=local-admin-secret
```

The app reads the Earth Engine key from `/app/service-account.json`. Compose
mounts `SERVICE_ACCOUNT_BIND_PATH` to that path. Direct local runs need that
path to exist.

## Redis

Run Redis locally:

```bash
docker run -d -p 6379:6379 redis:alpine
```

## Direct API And Worker

Start one worker queue:

```bash
LYRA_RUNNER_QUEUES=interactive \
uv run celery -A lyra_app.worker.celery_app worker --loglevel=info -Q interactive
```

Start the API:

```bash
uv run python -m lyra_app.main
```

The API defaults to port `5219`. Set `LYRA_PORT` to change it.

## Docker Compose

Run the development stack:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

The development stack starts the API, Redis, and two worker pools for
`interactive` and `batch`. The API mounts `/lyra_plugin_catalog`. Each worker
pool mounts its own `/lyra_plugins` volume.

## Plugin Catalog During Development

`LYRA_PLUGIN_REPOS` accepts comma-separated GitHub entries:

```text
owner/plugin-a,owner/plugin-b@main,https://github.com/owner/plugin-c@v0.1.0
```

`LYRA_PLUGIN_REPOS` does not support local filesystem paths. For local plugin
iteration, push a branch to GitHub, point `LYRA_PLUGIN_REPOS` at that branch,
and refresh the catalog.

The API syncs catalog repositories into `LYRA_PLUGIN_CATALOG_DIR`. Workers sync
and install runner repositories into `LYRA_PLUGIN_INSTALL_DIR`.

Set `LYRA_RUNNER_QUEUES` on each worker pool to the manifest queue names that
pool should import. If it is unset, the worker imports every installed plugin
metric, while Celery's `-Q` setting still controls which queue messages it
receives.

Refresh the catalog and restart worker pools:

```bash
curl -X POST 'http://localhost:5219/update-plugins?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

## Docs Site

Install docs dependencies from the lockfile:

```bash
npm ci --prefix docs
```

Preview the built site:

```bash
npm run build --prefix docs
npm run preview --prefix docs -- --host 127.0.0.1 --port 4321
```

The local docs URL is `http://127.0.0.1:4321/lyra/`.
