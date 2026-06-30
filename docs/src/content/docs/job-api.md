---
title: Job API
description: Submit metric jobs, stream events, fetch result metadata, and download files.
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

The `input` object is validated against the metric's compiled `request_schema`.
Unknown metrics return `404`. Invalid input returns `422`. Redis availability
errors return `503`.

`GET /data-types` exposes grouped wrapper schemas for `location` and `bounds`
inputs. Metric-specific payloads come from the selected metric's `/metrics`
entry, where Lyra has injected those wrapper schemas into the metric's compiled
request schema. Raw GeoJSON is accepted only inside a `geojson` wrapper's
`value`.

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

Table results return the full terminal table payload as JSON:

```json
{
  "kind": "table",
  "job_id": "job-id",
  "status": "succeeded",
  "index": ["area-1", "area-2"],
  "columns": ["value"],
  "data": [[42], [37]]
}
```

Failed and cancelled jobs also return terminal JSON with `200`:

```json
{
  "kind": "failed",
  "job_id": "job-id",
  "status": "failed",
  "error": {
    "type": "worker",
    "message": "Unexpected error"
  }
}
```

File results return stable metadata as JSON:

```json
{
  "kind": "file",
  "job_id": "job-id",
  "status": "succeeded",
  "file_path": "/lyra_data/cache/jobs/default/job-id/result.tif",
  "media_type": "image/tiff"
}
```

Repeated `GET /jobs/{job_id}/result` calls return the same stored terminal
payload until the job-store TTL expires.

## Download File Result

`GET /jobs/{job_id}/result/download` streams the produced file bytes for
terminal payloads with `kind: "file"`. The response uses the produced filename
and the `media_type` declared by the plugin's file result.

Downloading a file does not delete the stored result metadata. Repeated
downloads work while the file still exists. If the terminal file metadata exists
but the file is missing from disk, the download route returns `404`.

## Refresh Plugins

`POST /admin/plugin-catalog/refresh` syncs enabled plugin sources from
Lyra-owned state, refreshes the API manifest catalog, auto-assigns missing
metric routes with `plugins.default_queue`, and asks workers to restart.

The route requires Bearer authentication:

```bash
curl -X POST 'http://localhost:5219/admin/plugin-catalog/refresh?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

`timeout` is the number of seconds to wait for in-flight tasks before forcing worker shutdown.

## Python Clients

The `lyra-api` package wraps this HTTP flow. For end-to-end client workflows,
see [Python Client](../python-client/). For constructor options, method tables,
and exceptions, see [lyra-api](../lyra-api/).
