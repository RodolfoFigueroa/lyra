---
title: Job API
description: Submit metric jobs, stream events, and fetch JSON or file results.
---

Lyra's public execution API is job-first. Clients submit a metric request, receive a `job_id`, stream typed server-sent events, and fetch the terminal result.

## Submit A Job

`POST /jobs` accepts:

```json
{
  "metric": "tree_coverage",
  "input": {
    "data": {
      "data_type": "met_zone_code",
      "value": "19.1.01"
    }
  },
  "idempotency_key": "optional-client-key"
}
```

The `input` object is validated against the metric's v2 `request_schema`. Unknown metrics return `404`. Invalid input returns `422`. Redis availability errors return `503`.

Successful submissions return `202 Accepted`:

```json
{
  "job_id": "job-id",
  "metric": "tree_coverage",
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
  "metric": "tree_coverage",
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

## Python Client

The sync client wraps the same flow:

```python
from lyra.api import LyraAPIClient

client = LyraAPIClient("http://localhost:5219")
job = client.create_job("tree_coverage", {"data": {"data_type": "met_zone_code", "value": "19.1.01"}})

for event in client.iter_job_events(job.job_id):
    if event.event in {"succeeded", "failed", "cancelled"}:
        break

result = client.get_job_result(job.job_id)
```

For simple JSON-producing metrics, `client.process(metric, payload)` submits, waits for a terminal event, and returns the result value.
