---
title: REST API
description: Discover metrics, submit jobs, follow events, and retrieve retained results.
---

Lyra is job-first: discover a live metric contract, submit validated input,
follow one job, and copy the result before it expires. The generated
[HTTP reference](../../reference/generated/http/) contains the exact routes and
models for this release.

## Authentication

| Boundary | Credential |
| --- | --- |
| Health, data types, metrics, and metropolitan-zone lookup | None |
| `/jobs` and MCP | `Authorization: Bearer $LYRA_AGENT_API_KEY` |
| `/admin` | `Authorization: Bearer $LYRA_ADMIN_API_KEY` |

Do not give an external caller the admin key. Missing or malformed agent
authentication returns `401`; an incorrect token returns `403`.

## Discover the request

`GET /metrics` returns a catalog fingerprint and metric records. Fetch the
selected metric with `GET /metrics/{name}` and treat its `request_schema`,
`spatial_inputs`, and `output` as authoritative.

Spatial fields are wrapper objects. Supported wrappers are published by
`GET /data-types`; common forms include:

```json
{"data_type":"met_zone_code","value":"09.01"}
```

```json
{"data_type":"cvegeo_list","value":["090020001"]}
```

```json
{"data_type":"geojson","value":{"type":"FeatureCollection","features":[]}}
```

Database-backed wrappers are resolved to canonical GeoJSON before dispatch.

## Submit safely

`POST /jobs` accepts a metric name, its public input object, and an optional
idempotency key. Always provide a caller-owned key for work that may be retried.
The same key and validated request returns the original job; a different request
with the key returns `409`.

New REST and MCP submissions share a fixed-window quota. A `429` response
includes `Retry-After`; wait, then retry with the same key.

## Follow lifecycle

`GET /jobs/{job_id}` returns `queued`, `started`, `progress`, `succeeded`,
`failed`, or `cancelled`. The events route is an SSE stream that replays retained
events, supports `Last-Event-ID`, sends keepalives, and closes after a terminal
event. Repeat the agent header on polls and reconnects.

## Retrieve results

The descriptor route is the compact, provenance-rich view. It includes the
stable `lyra://results/{job_id}` reference, captured metric contract, plugin
identity, input, result shape, preview, column contracts, row identity, and
remaining lifetime.

- Table results stream from `/result/table.jsonl`.
- File results download from `/result/download`.
- `/result` returns the terminal SDK result model.

Lyra is temporary result storage, not an analytical database. Download retained
data and perform joins or statistics in the client. Before joining two tables,
require compatible non-null row identities and use each descriptor's declared
index field.

## Errors

| Status | Meaning |
| --- | --- |
| `401` / `403` | Missing, malformed, or invalid credential. |
| `404` | Metric, job, or retained result does not exist. |
| `409` | Idempotency conflict or wrong result-download kind. |
| `422` | Input does not match the selected metric schema. |
| `429` | Shared agent submission limit exceeded. |
| `503` | Redis, PostGIS, or spatial resolution is unavailable. |
