---
title: Local Development
description: Run Lyra locally, configure plugins, and work with the docs site.
---

This page is for contributors working in the Lyra repository. For first-run
environment setup, `.env` values, Redis, and the basic API/worker startup flow,
start with [Getting Started](../getting-started/).

## Workspace

Install the Python workspace before running tests, the API, or workers:

```bash
uv sync
```

The root project requires Python `>=3.11` and includes workspace packages under
`packages/*`.

## Direct API And Worker

Use direct processes when you are iterating on application or worker code and
want fast restarts. Run Redis and configure `.env` as described in
[Getting Started](../getting-started/), then start one worker queue:

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

Use Compose when you want the same mounted service-account path, plugin catalog
volume, and worker plugin volumes used by the deployment examples:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

The development stack starts the API, Redis, and two worker pools for
`interactive` and `batch`. The API mounts `/lyra_plugin_catalog`. Each worker
pool mounts its own `/lyra_plugins` volume.

## Plugin Catalog During Development

Plugin repositories must be reachable through GitHub-style
`LYRA_PLUGIN_REPOS` entries:

```text
owner/plugin-a,owner/plugin-b@main,https://github.com/owner/plugin-c@v0.1.0
```

`LYRA_PLUGIN_REPOS` does not support local filesystem paths. For local plugin
iteration, push a branch to GitHub, point `LYRA_PLUGIN_REPOS` at that branch,
and refresh the catalog. For repository entry formats and preflight checks, see
[Plugin Author Checklist](../plugin-author-checklist/).

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
