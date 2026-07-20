# Contributing to Lyra

This repository is a uv workspace containing the FastAPI application and four
public Python packages. Product documentation lives in `docs/` and is part of
the same change contract as code.

## Repository map

| Path | Responsibility |
| --- | --- |
| `lyra_app/` | HTTP routes, configuration, plugin catalog, job store, workers, MCP, and deployment runtime. |
| `packages/lyra_sdk/` | Public plugin, catalog, geometry, job, and runtime contracts. |
| `packages/lyra_api/` | Synchronous and asynchronous HTTP clients. |
| `packages/lyra_utils/` | Optional geospatial, date, and Earth Engine helpers. |
| `packages/lyra_tui/` | Terminal operator console. |
| `examples/lyra-plugin/` | Executable plugin used by the docs and integration tests. |
| `tests/` | Unit, contract, route, client, worker, and documentation tests. |
| `docs/` | Astro Starlight site and generated-reference tooling. |

Start route changes in `lyra_app/routes/`, manifest changes in
`packages/lyra_sdk/src/lyra/sdk/`, worker behavior in `lyra_app/worker.py`, and
client behavior in `packages/lyra_api/src/lyra/api/client/`.

## Environment

Use uv for Python environment and package management:

```bash
uv sync
```

For direct processes, provide `/lyra_data/config/lyra.toml`, the Earth Engine
service-account file, every `LYRA_POSTGRES_*` variable, and both API keys. Start
the API before workers so plugin state and routing exist:

```bash
uv run python -m lyra_app.main
uv run python -m lyra_app.worker_launcher interactive
```

The development Compose stack is the preferred integration environment:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

## Required verification

After changing Python, run all of these and fix failures caused by the change:

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest \
  --cov=lyra_app \
  --cov=lyra \
  --cov-report=term-missing \
  --cov-report=xml
```

For a focused iteration, run the affected test file first, then run the full
suite before handoff. Do not weaken lint, type, or coverage rules to make a
change pass.

Validate documentation with:

```bash
npm ci --prefix docs
npm run generate --prefix docs
npm run check --prefix docs
npm run build --prefix docs
```

Generated reference files are ignored. Change source types, docstrings,
configuration metadata, parsers, MCP contracts, or authored guides instead of
editing generated Markdown or JSON.

## Change discipline

- Keep API catalog behavior separate from worker code execution. API processes
  read manifests; workers install and import trusted plugin code.
- Update SDK models, routes, clients, tests, and docs together when a public
  contract changes.
- Keep one authoritative example or explanation and link to it elsewhere.
- Use Conventional Commit titles. Release Please versions the application,
  SDK, API client, utilities, and TUI independently.
- Application release tags (`lyra-app-vX.Y.Z`) define stable documentation
  versions. Package-only tags do not publish a new documentation site.

## Releases

Merges to `main` feed Release Please. Its combined release PR updates affected
components and the workspace lockfile. Merging that PR creates component tags
and GitHub Releases; an application release also publishes the container and a
stable documentation build from the exact application tag.

Use `fix` for patch changes, `feat` for minor changes, and `!` or a
`BREAKING CHANGE` footer for breaking changes. Below 1.0, breaking changes
advance the minor version.
