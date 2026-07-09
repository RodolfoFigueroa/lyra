---
title: Job API
description: Submit metric jobs, stream events, fetch result metadata, and download files.
---

Lyra's public execution API is job-first. Clients submit a metric request, receive a `job_id`, stream typed server-sent events, and fetch the terminal result.

Before creating a job, call `GET /metrics` and choose a metric from the
response's `metrics` list. The job `input` must match that metric's effective
`request_schema`. Every metric has at least one required spatial wrapper field.

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

## Result Descriptor Contract

`GET /jobs/{job_id}/result/descriptor` returns a compact descriptor for stored
terminal results instead of embedding the full terminal payload. The v1 result
reference format is:

```text
lyra://results/{job_id}
```

If the job is still queued or running, the descriptor route returns `202
Accepted` with a status envelope containing `job_id`, `status`, `updated_at`,
`result_ref`, and `detail`. Missing or expired results return `404`.

Descriptors keep the terminal result payload unchanged and include:

- `job_id`, terminal `status`, `result_kind`, and `result_ref`.
- `lifetime.expires_in_seconds` and, when Redis `PTTL` makes it exact,
  `lifetime.expires_at`.
- `raw.result_ref`, `raw.formats`, and `raw.terminal_json_path` for fetching the
  stored terminal JSON separately.
- `raw.jsonl_path` for successful table results. Table descriptors advertise
  both `terminal_json` and `jsonl` in `raw.formats`.
- `table` metadata for table results: row count, column count, ordered columns,
  and the preview index field.
- `preview.rows` as row-oriented JSON objects. Each row includes the result
  index under the named index field, `_result_index` unless a table column would
  collide with that name.
- `summary.columns` with per-column `count` and `null_count`; numeric columns
  also include `count`, `null_count`, `min`, `max`, and `mean`.
- `error` details for failed and cancelled terminal results when available.

The descriptor shape does not depend on table size. Full table JSON and file
metadata remain available through the stored terminal result while Redis retains
the job result key.

## Export Table JSONL

`GET /jobs/{job_id}/result/table.jsonl` streams successful table results as
JSONL using one JSON object per line. Each object includes the descriptor's
result index field and all table columns:

```jsonl
{"_result_index":"area-1","value":42}
{"_result_index":"area-2","value":37}
```

If a table already has a `_result_index` column, Lyra chooses a collision-free
index field name and reports it in the descriptor's `table.index_field`.

The JSONL route returns `404` when the result is missing or expired and `409`
when the stored result is not a table. File results continue to use the download
endpoint.

## Download File Result

`GET /jobs/{job_id}/result/download` streams the produced file bytes for
terminal payloads with `kind: "file"`. The response uses the produced filename
and the `media_type` declared by the plugin's file result.

Downloading a file does not delete the stored result metadata. Repeated
downloads work while the file still exists. If the terminal file metadata exists
but the file is missing from disk, the download route returns `404`.

## Admin Job Operations

Admin job operations require Bearer authentication.

For service liveness and operator overview routes, use `GET /health` and the
admin observability routes documented in [Operations](../operations/).

`GET /admin/jobs` returns recent job status snapshots from Lyra's Redis-backed
job index, newest-first. It supports `limit`, `status`, and `metric` query
parameters:

```bash
curl 'http://localhost:5219/admin/jobs?limit=25&status=started' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Expired jobs are pruned from normal responses.

`POST /admin/jobs/{job_id}/cancel` requests cancellation for active `queued`,
`started`, or `progress` jobs:

```bash
curl -X POST http://localhost:5219/admin/jobs/job-id/cancel \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Cancellation marks the job status as `cancelled`, emits a cancellation event,
and asks Celery to revoke the task. Terminal jobs are not overwritten; cancelling
an already terminal job returns `409`, and unknown or expired jobs return `404`.

## Refresh Plugins

`POST /admin/plugin-catalog/refresh` syncs enabled plugin sources from
Lyra-owned state, refreshes the API manifest catalog, auto-assigns missing
metric routes with `plugins.default_queue`, and reports whether workers should
be restarted.

Admin routes require Bearer authentication:

```bash
curl -X POST http://localhost:5219/admin/plugin-catalog/refresh \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"

curl -X POST 'http://localhost:5219/admin/workers/restart?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

`timeout` on `/admin/workers/restart` is the number of seconds to wait for
in-flight tasks before forcing worker shutdown.

## Python Clients

The `lyra-api` package wraps this HTTP flow. For end-to-end client workflows,
see [Python Client](../python-client/). For constructor options, method tables,
and exceptions, see [lyra-api](../lyra-api/).
