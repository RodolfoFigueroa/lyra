# Metric API

This API supports multiple metric prefixes through a single routing system.
The metric function is resolved from `endpoint_map` in `src/lyra/functions.py`.

Example:

```python
endpoint_map = {
		"tree_coverage": calculate,
		"urban_area": calculate_two,
}
```

## Install

```bash
uv sync
```

## Run

```bash
uv run uvicorn lyra-api
```

## Documentation

### REST API Documentation

Interactive API documentation is automatically generated and available at:
- **Swagger UI**: http://localhost:5219/docs
- **ReDoc**: http://localhost:5219/redoc

### WebSocket Endpoint Documentation

The WebSocket endpoint is documented using the AsyncAPI specification in [`asyncapi.yaml`](asyncapi.yaml).

To generate interactive HTML documentation:

```bash
# Install AsyncAPI CLI (requires Node.js)
npm install -g @asyncapi/cli

# Generate HTML documentation
asyncapi generate fromTemplate asyncapi.yaml @asyncapi/html-template -o docs/
```

This will create an interactive HTML document describing the `/ws/{metric}/geojson` endpoint, including:
- Available metrics and their parameters
- Message schemas and examples
- Error handling and response formats

## Endpoints

All endpoints are dynamic by metric prefix:

- `POST /{metric}/file`
- `POST /{metric}/geojson`
- `POST /{metric}/cvegeo`

For example, both of these are valid if configured in `endpoint_map`:

- `POST /tree_coverage/file`
- `POST /urban_area/file`

## Request Examples

Upload geopackage:

```bash
curl -X POST http://127.0.0.1:8000/tree_coverage/file \
	-F "file=@/path/to/polygons.gpkg"
```

GeoJSON body:

```bash
curl -X POST http://127.0.0.1:8000/tree_coverage/geojson \
	-H "Content-Type: application/json" \
	-d '{"type":"FeatureCollection","features":[]}'
```

CVEGEO body:

```bash
curl -X POST http://127.0.0.1:8000/tree_coverage/cvegeo \
	-H "Content-Type: application/json" \
	-d '{"cvegeo":["010010001"],"table_name":"my_table","crs":"EPSG:4326"}'
```

## Response

Every metric endpoint returns the dictionary produced by its metric function.

## Validation

The API returns `404` for unknown metric prefixes.
It returns `400` for malformed inputs, missing `cvegeo`, invalid geometries, or unsupported file formats.
