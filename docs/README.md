# API Documentation

## REST API

Interactive documentation is available when the server is running:

- **Swagger UI** (interactive): http://localhost:5219/docs
- **ReDoc** (clean HTML): http://localhost:5219/redoc

These are automatically generated from the FastAPI application and include:
- All REST endpoints (`/data_types`, `/metrics`, `/metrics/{metric_name}`, `/models`, `/models/{model_name}`, `/met_zone_code`, `/download_result/{download_id}`)
- Request/response schemas
- Parameter descriptions

## WebSocket API (AsyncAPI)

The WebSocket endpoint for metric computation is documented in [`asyncapi.yaml`](../asyncapi.yaml) using the AsyncAPI 3.1.0 specification.

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
- Channel: `/ws/{metric}`
- Available metrics (accessibility_jobs, accessibility_services, temperature, temperature_raster, tree_coverage, urbanized_area)
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
2. **Channels**: One per metric (`/ws/accessibility_jobs`, `/ws/accessibility_services`, `/ws/temperature`, `/ws/temperature_raster`, `/ws/tree_coverage`, `/ws/urbanized_area`)
3. **Messages**:
   - `AccessibilityJobsRequest`: Client request payload structure
   - `AccessibilityServicesRequest`: Client request payload structure
   - `TemperatureRequest`: Client request payload structure
   - `TemperatureRasterRequest`: Client request payload (returns a GeoTIFF file)
   - `TreeCoverageRequest`: Client request payload structure
   - `UrbanizedAreaRequest`: Client request payload structure
   - `QueuedMessage`: Server confirmation (task queued)
   - `ResultMessage`: Server result (computation complete)
   - `ErrorMessage`: Server error response
   - `ValidationErrorMessage`: Server validation error response

### Continuous Integration

To add AsyncAPI validation to your CI pipeline (e.g., GitHub Actions):

```yaml
- name: Validate AsyncAPI
  run: |
    npm install -g @asyncapi/cli
    asyncapi validate asyncapi.yaml
```

This ensures the specification remains valid whenever the repository is updated.
