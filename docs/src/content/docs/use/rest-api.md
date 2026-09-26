---
title: REST API
description: Discover metrics, submit jobs, poll status, and retrieve retained results.
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
{"data_type":"geojson","value":{"type":"FeatureCollection","features":[{"type":"Feature","id":"area","geometry":{"type":"Polygon","coordinates":[[[-99.2,19.3],[-99.1,19.3],[-99.1,19.4],[-99.2,19.3]]]},"properties":{}}],"crs":{"type":"name","properties":{"name":"EPSG:4326"}}}}
```

Database-backed wrappers are resolved to canonical GeoJSON before dispatch.
Locations accept one or more Polygon/MultiPolygon features. Bounds accept exactly
one Polygon feature. Neither accepts Point; bounds also reject MultiPolygon.
Inputs carry their declared CRS and are not automatically reprojected to EPSG:4326.

Ordinary inputs belong in `input.parameters`; spatial fields remain at the input
root. For the configured example plugin:

```json
{
  "metric": "smoke_table_metric",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"value": 7}
  }
}
```

A declared parameter model requires an object, including `{}` when all fields have
defaults. A parameterless metric omits `parameters` entirely; sending it is invalid.
The API rejects schema violations with `422`. Worker-only semantic validation can
subsequently fail an accepted job with `invalid_input`.

## Submit safely

`POST /jobs` accepts a metric name and its public input object. Every submission
creates an independent job. There is no deduplication, submission quota, automatic
execution retry, or remote cancellation. A failed or timed-out submission may have
been accepted; submitting again may duplicate the computation. Clients never
resubmit automatically.

Defaults are applied during worker execution. Provenance retains the unresolved
submitted input without default injection.

## Follow lifecycle

`GET /jobs/{job_id}` returns `queued`, `running`, `succeeded`, or `failed`. The shared status includes `job_id`, `metric`, `created_at`, nullable
`started_at` and `completed_at`, `updated_at`, optional `progress`, and optional
`error`. Progress is a latest snapshot and may be absent for the entire run.
Poll immediately, then approximately every five seconds. Fetch the terminal
result when status becomes succeeded, failed.

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
| `409` | Wrong result-download kind. |
| `422` | Input does not match the selected metric schema. |
| `503` | Redis, PostGIS, or spatial resolution is unavailable. |

Pending descriptors return `202`; terminal descriptors use schema version 2. Missing
or expired jobs return `404`. Successes and failures share the configured retention.
