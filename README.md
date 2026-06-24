# Lyra API

REST API for computing accessibility and land-use metrics for spatial units in Mexico. Metrics run as async Celery jobs backed by Redis; spatial computation uses Google Earth Engine and OSMnx.

## Prerequisites

- Google Earth Engine service account key saved as a JSON file.
- A `.env` file in the project root with at least:

```env
EARTHENGINE_PROJECT=your-gee-project-id
```

Optional logging settings:

```env
LYRA_LOG_LEVEL=INFO
LYRA_LOG_FILE=logs/lyra.log
```

If `LYRA_LOG_FILE` is set, Lyra writes its internal logs to that file instead of standard output.

## Install

```bash
uv sync
```

## Run

Start Redis (required for the task queue):

```bash
docker run -d -p 6379:6379 redis:alpine
```

Start the Celery worker (in a separate terminal):

```bash
uv run celery -A lyra.worker.celery_app worker --loglevel=info
```

Start the API server:

```bash
uv run lyra
```

### Docker (recommended)

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

This starts the API (`lyra`), Redis, and the Celery worker together.

## Endpoints

### REST

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/data_types` | List accepted input data types |
| `GET` | `/metrics` | List available metrics and request/result schemas |
| `GET` | `/metrics/{metric_name}` | Get request/result schemas for a single metric |
| `POST` | `/jobs` | Submit a metric job |
| `GET` | `/jobs/{job_id}` | Fetch current job status |
| `GET` | `/jobs/{job_id}/events` | Stream queued, progress, and terminal events with SSE |
| `GET` | `/jobs/{job_id}/result` | Fetch a terminal JSON result or file |
| `GET` | `/met_zone_code` | Look up a metropolitan zone code by name |

Available metrics: `accessibility_jobs`, `accessibility_services`, `temperature`, `temperature_raster`, `tree_coverage`, `urbanized_area`.

> **Note:** `temperature_raster` returns a GeoTIFF file instead of JSON. The result is downloaded via `GET /jobs/{job_id}/result` with `Content-Type: image/tiff`.

## Job API Usage

The job endpoint follows a submit/status/events/result flow:

1. `POST /jobs` with `metric`, `input`, and optional `idempotency_key`.
2. Receive `202 Accepted` with a `job_id` and links.
3. Stream `GET /jobs/{job_id}/events` as `text/event-stream`.
4. Fetch the terminal result via `GET /jobs/{job_id}/result`.

Submit a job:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{"metric":"tree_coverage","input":{"data":{"data_type":"met_zone_code","value":"19.1.01"}}}'
```

Stream events:

```bash
curl -N http://localhost:5219/jobs/{job_id}/events
```

Fetch the terminal result:

```bash
curl http://localhost:5219/jobs/{job_id}/result
```

## Documentation

### REST API

Interactive documentation is available while the server is running:

- **Swagger UI**: http://localhost:5219/docs
- **ReDoc**: http://localhost:5219/redoc
