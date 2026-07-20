---
title: Job API
description: Submit authenticated jobs, inspect retained provenance, and download results.
---

Lyra's execution API is job-first. Discovery is public, while every job
lifecycle route requires the agent credential. Choose a metric, submit with an
idempotency key, follow one `job_id`, and download data before it expires.

## Authentication Boundary

| Access | Routes | Credential |
| --- | --- | --- |
| Public | `GET /live`, `GET /ready`, `GET /data-types`, `GET /metrics`, `GET /metrics/{metric_name}`, `GET /lookups/met-zones` | None |
| Agent | `POST /jobs` and every `GET /jobs/{job_id}...` status, event, terminal JSON, descriptor, JSONL, and file-download route; configured MCP mount | `Authorization: Bearer $LYRA_AGENT_API_KEY` |
| Admin | Every `/admin/*` route | `Authorization: Bearer $LYRA_ADMIN_API_KEY` |

The keys are separate trust boundaries. Never give an agent the admin key.
Missing or malformed agent auth returns `401`; an invalid token returns `403`.

## Discover And Submit

Resolve a name first:

```bash
curl --get https://lyra.example.com/lookups/met-zones \
  --data-urlencode 'name=Valle de México'
```

Use canonical `cve_met`, then fetch `GET /metrics` or
`GET /metrics/{metric_name}`. Treat `request_schema`, `spatial_inputs`, and
`output` as the submission contract. `GET /data-types` provides wrapper schemas
for direct REST clients.

`POST /jobs` accepts:

```json
{
  "metric": "METRIC_NAME",
  "input": {
    "SPATIAL_FIELD": {"data_type": "met_zone_code", "value": "09.01"}
  },
  "idempotency_key": "client-generated-operation-key"
}
```

```bash
curl -X POST https://lyra.example.com/jobs \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d @request.json
```

Lyra validates the compiled metric schema and resolves spatial wrappers before
dispatch. Unknown metrics return `404`, invalid input `422`, and unavailable
dependencies `503`. An accepted request returns `202`:

```json
{
  "job_id": "job-id",
  "metric": "METRIC_NAME",
  "status": "queued",
  "reused": false,
  "links": {
    "self": "/jobs/job-id",
    "events": "/jobs/job-id/events",
    "result": "/jobs/job-id/result"
  }
}
```

## Idempotency And Rate Limits

The key binds atomically to the metric and validated public input for the
job-store lifetime. The same key and request returns `202`, the original
`job_id`, and `reused: true`; no task is dispatched. A different request with
that key returns `409` with `detail.code: "idempotency_conflict"` and the bound
job ID. Keep the key across ambiguous network retries. Keys expire and are not
a durable operation registry.

REST and MCP share a configurable fixed window. Defaults are 10 new accepted
jobs per 60 seconds. Equivalent idempotent replays consume no capacity. An
over-limit REST request returns `429`, a `Retry-After` header, and:

```json
{
  "detail": {
    "code": "rate_limited",
    "message": "Agent job submission limit exceeded. Please try again later.",
    "retry_after_seconds": 17
  }
}
```

Wait at least the advertised interval, then retry with the same key. Operators
configure positive `limit` and `window_seconds` values under
`[agent_submission_limit]`.

## Poll Status Or Events

`GET /jobs/{job_id}` returns `queued`, `started`, `progress`, `succeeded`,
`failed`, or `cancelled`. `GET /jobs/{job_id}/events` streams typed SSE records
and accepts `Last-Event-ID` to resume. It replays retained events, emits idle
keepalives, and closes after a terminal event. Repeat the agent header on every
poll and reconnect. Missing or expired jobs return `404`.

## Terminal Results And Descriptors

`GET /jobs/{job_id}/result` returns stored terminal JSON and `404` before it
exists or after expiry. Successful tables contain `index`, `columns`, and
row-major `data`; files contain server-side `file_path` and `media_type`; failed
and cancelled results contain errors.

Prefer `GET /jobs/{job_id}/result/descriptor` for agent workflows. Active jobs
return `202` with status and `result_ref`. A terminal descriptor includes:

- schema version, job ID, status, result kind, completion time, and
  `lyra://results/{job_id}` reference;
- immutable provenance: metric, catalog fingerprint, plugin name/version,
  validated unresolved input, captured output declaration, creation time, and
  authoritative row identity when known;
- remaining lifetime and optional exact expiry;
- raw terminal JSON formats and JSONL path for tables;
- table counts, ordered concrete `columns`, matching `column_contracts`,
  collision-free synthetic `index_field`, and row identity;
- bounded preview, per-column summaries, file metadata, or error details.

Concrete `column_contracts` declare names, types, units, descriptions, and
nullability. The captured provenance output keeps batch source metadata.
`table.index_field` is normally `_result_index`, but Lyra chooses a different
name if that collides with a metric column. Provenance remains unchanged across
catalog refreshes; verify it before comparing runs.

## Downloads And Retention

`GET /jobs/{job_id}/result/table.jsonl` streams successful tables as
`application/x-ndjson`, one object per line. Every object contains the
descriptor's index field and all concrete columns:

```jsonl
{"_result_index":"area-1","value":42}
{"_result_index":"area-2","value":37}
```

It returns `404` after expiry and `409` for non-table results.
`lyra_download_result` exposes the route as an absolute URL built from
`api.public_base_url`; the agent Bearer token is still required.

`GET /jobs/{job_id}/result/download` streams a successful file result. It
returns `409` for non-files and `404` if metadata expired or the file is gone.

Status, events, provenance, terminal results, descriptors, and idempotency
records expire according to `[job_store].ttl_seconds`. Downloads do not make
them durable. Copy needed JSONL or files externally and perform statistical
analysis outside Lyra.

## Admin Job Operations

Operators use the separate admin token. `GET /admin/jobs` lists retained jobs;
`POST /admin/jobs/{job_id}/cancel` requests cancellation. These admin-only
operations are not exposed through MCP.

```bash
curl 'https://lyra.example.com/admin/jobs?limit=25&status=started' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

See [lyra-api](../lyra-api/) for Python methods and [MCP Agent
Bridge](../mcp-agent-bridge/) for the agent sequence.
