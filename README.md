# Lyra API

Lyra is a REST API for computing accessibility and land-use metrics for spatial units in Mexico. Metrics run as async Celery jobs backed by Redis; spatial computation uses Google Earth Engine and OSMnx.

## Documentation

The written project docs are published with Astro Starlight:

- Hosted docs: https://rodolfofigueroa.github.io/lyra/
- Local docs: `npm run dev --prefix docs`
- Develop Lyra: https://rodolfofigueroa.github.io/lyra/contributor-guide/
- Build a plugin: https://rodolfofigueroa.github.io/lyra/plugin-quickstart/
- Use the API: https://rodolfofigueroa.github.io/lyra/job-api/

When the API server is running, FastAPI also exposes generated OpenAPI references:

- Swagger UI: http://localhost:5219/docs
- ReDoc: http://localhost:5219/redoc

## Quick Start

Install dependencies:

```bash
uv sync
```

Create local host files for the server config and secrets:

```text
lyra_data/config/lyra.toml
secrets/admin_api_key
secrets/postgres_password
secrets/service-account.json
```

Start from the checked-in example:

```bash
mkdir -p lyra_data/config secrets
cp lyra.toml.example lyra_data/config/lyra.toml
cp .env.example .env
```

The Compose stack mounts `lyra.toml` and each secret as an individual read-only
file. The `lyra_data` named volume remains writable for Lyra-owned runtime
state, including `/lyra_data/state/plugins.toml`, plugin checkouts, runner
installs, cache files, and optional logs.

The config file owns Redis, database, Earth Engine, worker pools, plugin queue
policy, logging, job TTL, and API host/port settings. Plugin repositories and
metric queue assignments are managed through admin API endpoints and persisted
by Lyra in `/lyra_data/state/plugins.toml`.

Run the development Compose stack:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

For direct local processes, start Redis, then launch a configured worker and the
API:

```bash
docker run -d -p 6379:6379 redis:alpine
uv run python -m lyra_app.worker_launcher interactive
uv run python -m lyra_app.main
```

Both commands read `/lyra_data/config/lyra.toml`.

After the stack is running, add plugins through the admin API:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer $(cat secrets/admin_api_key)" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/plugin-repo@main"}'

curl -X POST 'http://localhost:5219/admin/plugin-catalog/refresh?timeout=30' \
  -H "Authorization: Bearer $(cat secrets/admin_api_key)"
```

## Job API

Submit a metric job:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{"metric":"METRIC_NAME","input":{"SPATIAL_FIELD":{"data_type":"cvegeo_list","value":["090020001"]}}}'
```

Choose `METRIC_NAME` from `GET /metrics`, and shape `input` according to that
metric's `request_schema`. Every metric includes at least one required spatial
wrapper field. Then use the returned `job_id` to stream events and fetch the
terminal result:

```bash
curl -N http://localhost:5219/jobs/{job_id}/events
curl http://localhost:5219/jobs/{job_id}/result
```

See the Starlight docs for plugin manifests, runner entrypoints, deployment shape, and operations notes.
