# Lyra API

Lyra is a REST API for computing accessibility and land-use metrics for spatial units in Mexico. Metrics run as async Celery jobs backed by Redis; spatial computation uses Google Earth Engine and OSMnx.

## Documentation

The written project docs are published with Astro Starlight:

- Hosted docs: https://rodolfofigueroa.github.io/lyra/
- Local docs: `npm run dev --prefix docs`
- Develop Lyra: https://rodolfofigueroa.github.io/lyra/contributor-guide/
- Build a plugin: https://rodolfofigueroa.github.io/lyra/plugin-quickstart/
- Use the API: https://rodolfofigueroa.github.io/lyra/job-api/
- Connect agents through MCP: https://rodolfofigueroa.github.io/lyra/mcp-agent-bridge/
- Run the operator TUI: https://rodolfofigueroa.github.io/lyra/tui/

When the API server is running, FastAPI also exposes generated OpenAPI references:

- Swagger UI: http://localhost:5219/docs
- ReDoc: http://localhost:5219/redoc

## Quick Start

Install dependencies:

```bash
uv sync
```

Create local host files for the server config and Earth Engine service account:

```text
lyra_data/config/lyra.toml
secrets/service-account.json
```

Start from the checked-in example:

```bash
mkdir -p lyra_data/config secrets
cp config.example.toml lyra_data/config/lyra.toml
cp .env.example .env
```

The Compose stack mounts `lyra.toml` and the service account as read-only
files, and passes Postgres, agent, and admin settings from `.env`. The `lyra_data` named
volume remains writable for Lyra-owned runtime state, including
`/lyra_data/state/plugins.toml`, plugin checkouts, runner installs, cache
files, and optional logs.

The config file owns Redis, Earth Engine, worker pools, plugin queue policy,
logging, job TTL, public API base URL, submission limits, and API host/port
settings. Postgres connection settings plus the separate agent and admin API
keys come from environment variables. `LYRA_AGENT_API_KEY` authenticates MCP
and every `/jobs` route; `LYRA_ADMIN_API_KEY` is only for `/admin` routes. Plugin
repositories can be seeded on first startup with `plugins.initial_repos`, then
are managed through admin API endpoints and persisted with metric queue
assignments in `/lyra_data/state/plugins.toml`.

Run the development Compose stack:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

After the API is running, open the operator TUI in another terminal:

```bash
LYRA_ADMIN_API_KEY=... uv run lyra-tui --host localhost:5219 --no-secure
```

The TUI connects to the running API; it does not start Redis, the API, or
workers itself. Without an admin key it can show public readiness only.

For direct local processes, start Redis and the API, wait for `/ready`, then
launch a configured worker:

```bash
docker run -d -p 6379:6379 redis:alpine
uv run python -m lyra_app.main
uv run python -m lyra_app.worker_launcher interactive
```

Both commands read `/lyra_data/config/lyra.toml`.

After the stack is running, add plugins through the admin API:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/plugin-repo@main"}'

curl -X POST http://localhost:5219/admin/plugin-catalog/refresh \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"

curl -X POST 'http://localhost:5219/admin/workers/restart?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

## Job API

Public discovery does not require a token. Resolve a metropolitan-zone name and
choose a metric before submitting:

```bash
curl --get http://localhost:5219/lookups/met-zones \
  --data-urlencode 'name=Valle de México'
curl http://localhost:5219/metrics
```

Every job lifecycle route requires the agent Bearer token. Use an idempotency
key so retrying the same validated request returns the original `job_id`:

```bash
curl -X POST http://localhost:5219/jobs \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"metric":"METRIC_NAME","input":{"SPATIAL_FIELD":{"data_type":"met_zone_code","value":"09.01"}},"idempotency_key":"client-generated-key"}'
```

Choose `METRIC_NAME` from `GET /metrics`, and shape `input` according to that
metric's `request_schema`. Every metric includes at least one required spatial
wrapper field. Then use the returned `job_id` to stream events and fetch the
terminal result:

```bash
curl -N http://localhost:5219/jobs/{job_id}/events \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}"
curl http://localhost:5219/jobs/{job_id}/result/descriptor \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}"
```

New REST and MCP submissions share a configurable fixed-window limit (10 per
60 seconds by default). Results expire with the configured job-store TTL.
Download table JSONL while the descriptor is live and perform statistical
analysis in the external client.

See the Starlight docs for plugin manifests, runner entrypoints, deployment shape, and operations notes.
For Codex and other agent runtimes, see the MCP bridge docs for the stable tool
sequence, Bearer-token setup, result references, and JSONL analysis handoff.
