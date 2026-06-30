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

Create the server config and secret files under `/lyra_data`:

```text
/lyra_data/config/lyra.toml
/lyra_data/secrets/admin_api_key
/lyra_data/secrets/postgres_password
/lyra_data/secrets/service-account.json
```

Start from the checked-in example:

```bash
mkdir -p /lyra_data/config /lyra_data/secrets
cp lyra.toml.example /lyra_data/config/lyra.toml
```

The config file owns Redis, database, Earth Engine, plugin repositories, metric
queue assignments, worker pools, logging, job TTL, and API host/port settings.
Secrets are referenced by file path from the TOML file instead of stored inline.
By default, Lyra reads secret files from `/lyra_data/secrets`, so Docker users
can mount or copy local secret files to those container paths without editing
the commented path settings.

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
