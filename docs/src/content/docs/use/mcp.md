---
title: MCP workflow
description: Discover, submit once, inspect bounded results, and download retained data.
---

Connect to the configured MCP endpoint with `Authorization: Bearer` using the
credential in `LYRA_AGENT_API_KEY`. The [generated MCP reference](../../reference/generated/mcp/)
contains the strict input and serialization schemas.

## Discover, submit, inspect, download

1. Search with `lyra_search_metrics`; use `lyra_list_metrics` for catalog inventory.
   Read the selected contract with `lyra_get_metric`. Resolve a place name with
   `lyra_lookup_met_zone` when you need its raw metropolitan-zone code.
2. Call `lyra_run_metric` with `metric`, `met_zone_code`, optional `parameters`,
   and a caller-owned `idempotency_key`. Submission never waits or reads completion.
3. Call `lyra_get_job_result` with only `result_ref`. For `queued` or `running`,
   wait two seconds before the next inspection. Failed and cancelled jobs are
   successful observations (`isError: false`) with terminal status.
4. Call `lyra_download_result` with only `result_ref` to obtain an authenticated
   HTTP handoff. Tables use `/jobs/{job_id}/result/table.jsonl`; files use
   `/jobs/{job_id}/result/download`. Send the agent Bearer credential yourself.

`reused` means an equivalent idempotent submission reused the original job ID.
It says nothing about caching or completion. The same key with different inputs
returns `idempotency_conflict`. Submission quotas return `rate_limited` with retry
information. Job operations have a fixed 30-second backend deadline and perform
no internal polling, retry, or automatic resubmission. A submission timeout may
occur after acceptance: retry with the **original idempotency key**.

The result tools do not accept `wait_seconds`. The separate metadata and preview
tools have been removed. REST and Python-client waiting remain available.

## Inspection limits and complete metadata

Inspection returns at most 10 preview rows and 20 data columns, plus the row
identity field. Original dimensions are reported separately. Names, contracts,
statistics, and preview cells use the same retained columns in original order.
Display strings are limited to 500 characters, including a final ellipsis when
shortened. Identifiers, field names, and numbers are never shortened; oversized
items may be omitted. Collection-valued preview cells are omitted explicitly.

`truncation` records omitted rows, omitted columns, shortened strings, and omitted
sections. The complete serialized MCP result is limited to 64 KiB, counting both
text and structured copies as compact UTF-8 JSON. Further trimming removes input
details, trailing preview rows, trailing column bundles, detailed errors, then
optional file metadata. Mandatory status, identifiers, known timestamps,
polling or descriptor access, and truncation information remain. If those alone
exceed the limit, the tool returns `result_response_too_large`.

Compact provenance retains metric/plugin identity, catalog fingerprint, creation
time, and row identity. Small scalar inputs remain; collections become type/count
summaries and GeoJSON becomes feature count and CRS without coordinates. The
stored provenance is unchanged. Use the `descriptor` HTTP handoff with the agent
Bearer credential to retrieve the complete REST descriptor, including full input
and output declarations. File metadata exposes media type and access instructions,
never a local artifact path.

Unknown timestamps or provenance are null. Retained terminal payloads take
precedence over stale or missing status. Failed/cancelled status without a payload
still reports that known outcome without inventing result metadata.

## Representative payloads

These are the shared payloads in `structuredContent` and the JSON text content;
optional null fields may also be present. Authentication instructions name an
environment variable and never contain its secret value.

Submission:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","reused":false,"next_tool":"lyra_get_job_result"}
```

Queued:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","status":"queued","metric":"population","created_at":"2026-09-24T12:00:00Z","updated_at":"2026-09-24T12:00:00Z","poll_after_seconds":2,"next_tool":"lyra_get_job_result","truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```

Running with progress:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","status":"running","metric":"population","created_at":"2026-09-24T12:00:00Z","updated_at":"2026-09-24T12:00:03Z","started_at":"2026-09-24T12:00:01Z","progress":{"timestamp":"2026-09-24T12:00:03Z","stage":"compute","current":1,"total":10},"poll_after_seconds":2,"next_tool":"lyra_get_job_result","truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```

Table success without retained provenance:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","status":"succeeded","result_kind":"table","completed_at":"2026-09-24T12:00:10Z","lifetime":{"expires_in_seconds":3600},"descriptor":{"method":"GET","url":"https://lyra.example/api/jobs/job-1/result/descriptor","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}},"table":{"row_count":1,"column_count":1,"columns":["population"],"column_contracts":[],"index_field":"_result_index"},"preview":{"index_field":"_result_index","rows":[{"_result_index":"090020001","population":100}],"row_limit":10,"truncated":false},"summary":{"kind":"table","row_count":1,"column_count":1,"columns":[{"name":"population","count":1,"null_count":0,"numeric":{"count":1,"null_count":0,"min":100,"max":100,"mean":100.0}}]},"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```

File success:

```json
{"job_id":"file-1","result_ref":"lyra://results/file-1","status":"succeeded","result_kind":"file","lifetime":{},"descriptor":{"method":"GET","url":"https://lyra.example/api/jobs/file-1/result/descriptor","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}},"file":{"media_type":"image/tiff","download":{"method":"GET","url":"https://lyra.example/api/jobs/file-1/result/download","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}}},"summary":{"kind":"file","columns":[]},"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```

Failure and cancellation (`isError: false`):

```json
{"job_id":"failed-1","result_ref":"lyra://results/failed-1","status":"failed","result_kind":"failed","lifetime":{},"descriptor":{"method":"GET","url":"https://lyra.example/api/jobs/failed-1/result/descriptor","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}},"error":{"message":"Execution failed"},"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```

```json
{"job_id":"cancelled-1","result_ref":"lyra://results/cancelled-1","status":"cancelled","result_kind":"cancelled","lifetime":{},"descriptor":{"method":"GET","url":"https://lyra.example/api/jobs/cancelled-1/result/descriptor","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}},"error":{"message":"Cancelled by administrator"},"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```

Unknown or expired reference (`isError: true`):

```json
{"error":{"code":"result_not_found","message":"This result reference is unknown or expired.","details":{"job_id":"old-1","result_ref":"lyra://results/old-1"}}}
```

Table and file download handoffs:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","status":"succeeded","format":"jsonl","media_type":"application/x-ndjson","lifetime":{"expires_in_seconds":3600},"lyra_api":{"method":"GET","url":"https://lyra.example/api/jobs/job-1/result/table.jsonl","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}}}
```

```json
{"job_id":"file-1","result_ref":"lyra://results/file-1","status":"succeeded","format":"file","media_type":"image/tiff","lifetime":{"expires_in_seconds":3600},"lyra_api":{"method":"GET","url":"https://lyra.example/api/jobs/file-1/result/download","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}}}
```

## Expiration and errors

Copy results before their reported lifetime ends. Active jobs have no terminal
retention deadline. An unknown or expired reference returns `result_not_found`;
there are no tombstones to distinguish the two. A succeeded status whose payload
is missing, or a missing file artifact, returns `result_unavailable`.

Downloading an active job returns `result_not_ready` with two-second polling
guidance. Failed/cancelled downloads return `result_not_downloadable`.
Infrastructure and deadline errors are retryable tool errors. These errors never
cause an automatic submission. If retained data is gone, decide whether the user
still needs a new execution before submitting one.
