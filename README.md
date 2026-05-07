# Lyra API

REST and WebSocket API for computing accessibility and land-use metrics for spatial units in Mexico. Metrics run as async Celery tasks backed by Redis; spatial computation uses Google Earth Engine and OSMnx.

## Prerequisites

- Google Earth Engine service account key saved as a JSON file.
- A `.env` file in the project root with at least:

```env
CELERY_BROKER_URL=redis://localhost:6379/0
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
| `GET` | `/metrics` | List available metrics and their parameters |
| `GET` | `/download_result/{download_id}` | Fetch a completed metric result |

### WebSocket

| Path | Description |
|------|-------------|
| `WS /ws/{metric}` | Submit a metric computation request |

Available metrics: `accessibility_jobs`, `accessibility_services`, `tree_coverage`, `urbanized_area`.

## WebSocket Usage

The WebSocket endpoint follows a request/response flow:

1. Connect to `ws://localhost:5219/ws/{metric}`.
2. Send a JSON payload with a `data` field (GeoJSON or a supported wrapper type).
3. Receive a `queued` message with a `task_id`.
4. Receive a `success` message with a `download_id` when computation completes.
5. Fetch the full result via `GET /download_result/{download_id}`.

Example using the `websockets` library:

```python
import asyncio, json, websockets

async def run():
    uri = "ws://localhost:5219/ws/tree_coverage"
    async with websockets.connect(uri) as ws:
        payload = {
            "data": {
                "data_type": "geojson",
                "value": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.0, 0.0]]]
                            },
                            "properties": {"cvegeo": "110010001001"}
                        }
                    ]
                }
            }
        }
        await ws.send(json.dumps(payload))

        queued = json.loads(await ws.recv())   # {"status": "queued", "task_id": "..."}
        result = json.loads(await ws.recv())   # {"status": "success", "download_id": "..."}
        print(queued, result)

asyncio.run(run())
```

Fetch the result:

```bash
curl http://localhost:5219/download_result/{download_id}
```

## Documentation

### REST API

Interactive documentation is available while the server is running:

- **Swagger UI**: http://localhost:5219/docs
- **ReDoc**: http://localhost:5219/redoc

### WebSocket API (AsyncAPI)

The WebSocket endpoint is documented in [`docs/asyncapi.yaml`](docs/asyncapi.yaml).

To generate a standalone interactive HTML document:

```bash
# Install AsyncAPI CLI (requires Node.js)
npm install -g @asyncapi/cli

# Generate HTML documentation
asyncapi generate fromTemplate docs/asyncapi.yaml @asyncapi/html-template -o docs/
```

To validate the spec:

```bash
asyncapi validate docs/asyncapi.yaml
```
