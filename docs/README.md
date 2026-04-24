# API Documentation

## REST API

Interactive documentation is available when the server is running:

- **Swagger UI** (interactive): http://localhost:5219/docs
- **ReDoc** (clean HTML): http://localhost:5219/redoc

These are automatically generated from the FastAPI application and include:
- All REST endpoints (`/data_types`, `/metrics`, `/download-result/{download_id}`)
- Request/response schemas
- Parameter descriptions

## WebSocket API (AsyncAPI)

The WebSocket endpoint for metric computation is documented in [`asyncapi.yaml`](../asyncapi.yaml) using the AsyncAPI 3.0.0 specification.

### Generating Interactive HTML Documentation

To generate a standalone interactive HTML document from the AsyncAPI specification:

#### Prerequisites

Install Node.js (if not already installed), then install the AsyncAPI CLI:

```bash
npm install -g @asyncapi/cli
```

#### Generate HTML

From the project root:

```bash
asyncapi generate fromTemplate asyncapi.yaml @asyncapi/html-template -o docs/
```

This will create `docs/index.html` with interactive documentation of:
- Channel: `/ws/{metric}/geojson`
- Available metrics (accessibility_jobs, accessibility_services, tree_coverage, urbanized_area)
- Message schemas (requests, responses, errors)
- Example payloads

#### Validate AsyncAPI Specification

To validate the AsyncAPI YAML without generating HTML:

```bash
asyncapi validate asyncapi.yaml
```

### Updating the AsyncAPI Specification

The `asyncapi.yaml` file documents:
1. **Server info**: WebSocket connection details
2. **Channel**: The `/ws/{metric}/geojson` endpoint with path parameters
3. **Messages**:
   - `MetricRequest`: Client request payload structure
   - `QueuedMessage`: Server confirmation (task queued)
   - `ResultMessage`: Server result (computation complete)
   - `ErrorMessage`: Server error response
   - `ValidationErrorMessage`: Server validation error response

When adding a new metric or changing the request/response format, update the relevant message schema in `asyncapi.yaml` to keep documentation in sync.

### Continuous Integration

To add AsyncAPI validation to your CI pipeline (e.g., GitHub Actions):

```yaml
- name: Validate AsyncAPI
  run: |
    npm install -g @asyncapi/cli
    asyncapi validate asyncapi.yaml
```

This ensures the specification remains valid whenever the repository is updated.
