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
