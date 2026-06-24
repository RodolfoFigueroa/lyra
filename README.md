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

Start Redis:

```bash
docker run -d -p 6379:6379 redis:alpine
```

Start a worker for the `interactive` queue:

```bash
LYRA_RUNNER_QUEUES=interactive \
uv run celery -A lyra_app.worker.celery_app worker --loglevel=info -Q interactive
```

Start the API server:

```bash
uv run python -m lyra_app.main
```

Or run the development Compose stack:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

## Job API

Submit a metric job:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{"metric":"METRIC_NAME","input":{}}'
```

Choose `METRIC_NAME` from `GET /metrics`, and shape `input` according to that metric's `request_schema`. Then use the returned `job_id` to stream events and fetch the terminal result:

```bash
curl -N http://localhost:5219/jobs/{job_id}/events
curl http://localhost:5219/jobs/{job_id}/result
```

See the Starlight docs for plugin manifests, runner entrypoints, deployment shape, and operations notes.
