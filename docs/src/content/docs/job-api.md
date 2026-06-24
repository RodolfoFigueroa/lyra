---
title: Job API
description: Submit metric jobs, stream events, and fetch JSON or file results.
---

Lyra's public execution API is job-first. Clients submit a metric request, receive a `job_id`, stream typed server-sent events, and fetch the terminal result.

Before creating a job, call `GET /metrics` and choose a metric from the current
catalog. The job `input` must match that metric's effective `request_schema`.
Every metric has at least one required spatial wrapper field.

## Submit A Job

`POST /jobs` accepts:

```json
{
  "metric": "METRIC_NAME",
  "input": {},
  "idempotency_key": "optional-client-key"
}
```

The `input` object is validated against the metric's v2 `request_schema`.
Unknown metrics return `404`. Invalid input returns `422`. Redis availability
errors return `503`.

`GET /data_types` exposes grouped wrapper schemas for `location` and `bounds`
inputs. Metric-specific payloads come from the selected metric's `/metrics`
entry, where Lyra has injected those wrapper schemas into the manifest's
declared spatial fields. Raw GeoJSON is accepted only inside a `geojson`
wrapper's `value`.

Before dispatching to workers, Lyra resolves spatial wrappers into canonical
GeoJSON dictionaries in `JobEnvelope.input`.

Successful submissions return `202 Accepted`:

```json
{
  "job_id": "job-id",
  "metric": "METRIC_NAME",
  "status": "queued",
  "links": {
    "self": "/jobs/job-id",
    "events": "/jobs/job-id/events",
    "result": "/jobs/job-id/result"
  }
}
```

`idempotency_key` is passed through to the worker in the `JobEnvelope`; it does not deduplicate submissions.

## Fetch Status

`GET /jobs/{job_id}` returns the current status snapshot:

```json
{
  "job_id": "job-id",
  "metric": "METRIC_NAME",
  "status": "progress",
  "updated_at": "2026-06-23T18:30:00Z"
}
```

Statuses are `queued`, `started`, `progress`, `succeeded`, `failed`, and `cancelled`.

## Stream Events

`GET /jobs/{job_id}/events` streams typed SSE records. Each record uses:

- `id`: Redis Stream ID.
- `event`: the `JobEvent.event` value.
- `data`: the full `JobEvent` JSON payload.

Example event payload:

```json
{
  "job_id": "job-id",
  "event": "progress",
  "timestamp": "2026-06-23T18:30:00Z",
  "data": {
    "message": "Loaded input geometry"
  }
}
```

Clients can reconnect with `Last-Event-ID` to resume after a known stream ID. The server replays stored events, waits for new ones, sends keepalive comments during idle periods, and closes after a terminal event.

## Fetch Result

`GET /jobs/{job_id}/result` returns `404` until a terminal result exists.

JSON results return the full `JobResult` payload:

```json
{
  "job_id": "job-id",
  "status": "succeeded",
  "result": {
    "value": 42
  },
  "result_type": null,
  "file_path": null,
  "error": null
}
```

Failed and cancelled jobs also return their terminal `JobResult` JSON with `200`.

File results return a file response when `result_type` is `file` and `file_path` points at the produced artifact. After the file response cleanup runs, only the stored result payload is deleted; status and events remain until the job-store TTL expires.

The current server response for file results uses the produced filename and `image/tiff` media type.

## Refresh Plugins

`POST /update-plugins` reclones or updates configured plugin repositories, refreshes the API manifest catalog, and asks workers to restart.

The route requires Bearer authentication:

```bash
curl -X POST 'http://localhost:5219/update-plugins?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

`timeout` is the number of seconds to wait for in-flight tasks before forcing worker shutdown.

## Python Client

The sync client wraps the same flow:

```python
from lyra.api import LyraAPIClient

client = LyraAPIClient("localhost:5219", secure=False)
metrics = client.get_metrics()
metric_name = metrics[0].name
payload = {
    "SPATIAL_FIELD": {
        "data_type": "cvegeo_list",
        "value": ["090020001"],
    }
}
job = client.create_job(metric_name, payload)

for event in client.iter_job_events(job.job_id):
    if event.event in {"succeeded", "failed", "cancelled"}:
        break

result = client.get_job_result(job.job_id)
```

For simple JSON-producing metrics, `client.process(metric, payload)` submits, waits for a terminal event, and returns the result value.
