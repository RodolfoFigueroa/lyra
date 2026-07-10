---
title: MCP Agent Bridge
description: Expose Lyra metrics to agents through authenticated, typed MCP tools and reproducible JSONL handoffs.
---

Lyra mounts a stateless Streamable HTTP server implemented with the official
Python MCP SDK. An agent can resolve a metropolitan zone, search the public
metric catalog, run a metric, poll one result reference, inspect provenance,
and hand an authenticated JSONL download to external analysis code.

Lyra executes metrics and temporarily retains results. It does not expose admin
operations, SQL, or statistical analysis through MCP.

## Operator Setup

Enable MCP and configure the absolute URL external clients use to reach Lyra:

```toml
[api]
public_base_url = "https://lyra.example.com"

[mcp]
enabled = true
mount_path = "/mcp"
```

`api.public_base_url` may include a public path prefix. It must use HTTPS in
production and be reachable from the external analysis runtime. Lyra uses it to
create absolute result-download URLs.

Set independent secrets on the API process:

```text
LYRA_AGENT_API_KEY=replace-with-agent-secret
LYRA_ADMIN_API_KEY=replace-with-admin-secret
```

`LYRA_AGENT_API_KEY` authenticates MCP and every `/jobs` route. Never give an
agent `LYRA_ADMIN_API_KEY`.

Codex connects to the official Streamable HTTP endpoint:

```toml
[mcp_servers.lyra]
url = "https://lyra.example.com/mcp"
bearer_token_env_var = "LYRA_AGENT_API_KEY"
```

Set `LYRA_AGENT_API_KEY` in the environment that starts Codex. For local
development use `http://localhost:5219/mcp`. Missing or malformed Bearer auth
returns `401`; a wrong credential, including an admin-only credential, returns
`403`.

## Strict Tools

Tools publish MCP input and output JSON Schemas. Inputs reject unknown fields
and wrong primitive types. Respect declared ranges: metric search and list
`limit` values are 1 through 20, run wait time is 0 through 10 seconds, and poll
wait time is 0 through 30. Invalid arguments return structured issues and, when
the correction is deterministic, suggested arguments.

| Tool | Use |
| --- | --- |
| `lyra_lookup_met_zone` | Resolve a natural-language name to canonical `cve_met` and `nom_met`. |
| `lyra_list_metrics` | Page through a compact catalog inventory when the user explicitly asks what is available. |
| `lyra_search_metrics` | Search public catalog names, descriptions, inputs, outputs, and units. |
| `lyra_get_metric` | Inspect one request schema, spatial mapping, and output declaration. |
| `lyra_run_metric` | Submit one metric for a raw metropolitan-zone code. |
| `lyra_get_job_result` | Poll the same `lyra://results/{job_id}` reference until terminal. |
| `lyra_get_result_metadata` | Read provenance, lifetime, shape, columns, summary, and errors. |
| `lyra_get_result_preview` | Read provenance, preview rows, and summary without hydrating a table. |
| `lyra_download_result` | Obtain an absolute authenticated JSONL handoff for a table. |

## One Agent Workflow

For a task-specific request, start with location lookup and a focused metric
search:

```json
{"name": "Valle de México"}
```

```json
{"query": "clinic accessibility", "limit": 5}
```

Do not use empty, single-letter, or generic searches to enumerate the catalog.
If the user explicitly asks which or all metrics are available, use the compact
inventory tool instead:

```json
{"limit": 20}
```

The response includes `total_count`, up to 20 metric names and compact
descriptions, and `next_cursor`. Pass a non-null cursor back unchanged to read
the next page. A cursor is bound to the catalog fingerprint; if the catalog
changes, restart without a cursor. Use inventory only for explicit catalog
requests or after focused searches return no candidates.

Use the lookup's `cve_met` as `met_zone_code`. Inspect the selected metric
before running it:

```json
{"metric": "selected_metric_name"}
```

`lyra_get_metric` is authoritative for non-spatial parameters. MCP inserts the
metric's sole spatial field, so do not include a spatial field, GeoJSON, or
census-unit list in `parameters`.

Submit with a caller-generated idempotency key:

```json
{
  "metric": "selected_metric_name",
  "met_zone_code": "09.01",
  "parameters": {"year": 2025},
  "idempotency_key": "analysis-run-2025-access",
  "wait_seconds": 2
}
```

The key binds to the validated metric and input for the result lifetime. A
retry with the same key and request returns the original `job_id` and
`reused: true`; it creates no job and consumes no submission capacity. Using
the key with a different request returns `idempotency_conflict`. Keep the same
key across network retries.

Active work returns one continuation reference:

```json
{
  "status": "running",
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "poll_after_seconds": 1,
  "next_tool": "lyra_get_job_result",
  "reused": false
}
```

Wait at least `poll_after_seconds`, then call the returned tool with the same
reference:

```json
{"result_ref": "lyra://results/job-1", "wait_seconds": 30}
```

Do not resubmit because a poll is still running. Terminal responses are compact
descriptors for succeeded, failed, or cancelled work.

## Provenance And Expiry

Before comparing or downloading results, inspect:

- `provenance.metric`, `catalog_fingerprint`, plugin name/version, validated
  public `input`, captured `output`, and `created_at`;
- `provenance.row_identity` and `table.row_identity`, when available;
- `table.index_field`, ordered `columns`, and concrete `column_contracts`;
- `lifetime.expires_in_seconds` and `lifetime.expires_at`.

Provenance is captured when Lyra accepts the job and survives catalog refreshes
unchanged. Results and idempotency records expire with the job-store TTL.
Download needed data before expiry. An expired reference must be rerun with a
new key if the data is still needed.

## Limit And Retry

New REST and MCP submissions share a fixed-window quota. Defaults are 10
accepted submissions per 60 seconds; operators can configure both values in
`[agent_submission_limit]`. An MCP `rate_limited` error includes
`retry_after_seconds`. Wait that long and retry with the same idempotency key.
Idempotent replays consume no capacity.

## Authenticated JSONL Handoff

After a successful table result, call:

```json
{"result_ref": "lyra://results/job-1"}
```

`lyra_download_result` returns an absolute handoff, not raw rows or a relative
path:

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "status": "succeeded",
  "format": "jsonl",
  "media_type": "application/x-ndjson",
  "lyra_api": {
    "method": "GET",
    "url": "https://lyra.example.com/jobs/job-1/result/table.jsonl",
    "authentication": {
      "scheme": "Bearer",
      "credential_env_var": "LYRA_AGENT_API_KEY"
    }
  },
  "expires_in_seconds": 480
}
```

The external runtime sends `Authorization: Bearer $LYRA_AGENT_API_KEY` to that
URL. JSONL is the table format. Python clients can instead call
`download_result(result_ref, path, format="jsonl")` with `agent_api_key` set.

## Safe Two-Metric Correlation

This example selects descriptor-declared numeric columns and refuses to join
results without the same authoritative row identity:

```python
import os

from lyra.api import LyraAPIClient

client = LyraAPIClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)
left_ref = "lyra://results/job-left"
right_ref = "lyra://results/job-right"
left_descriptor = client.get_result_descriptor(left_ref)
right_descriptor = client.get_result_descriptor(right_ref)


def analysis_contract(descriptor):
    if descriptor.status != "succeeded" or descriptor.table is None:
        raise ValueError("correlation requires a successful table result")
    if descriptor.provenance is None or descriptor.table.row_identity is None:
        raise ValueError("result lacks provenance or authoritative row identity")
    numeric = [
        column.name
        for column in descriptor.table.column_contracts
        if column.type in {"number", "integer"}
    ]
    if len(numeric) != 1:
        raise ValueError(f"select one declared numeric column from {numeric}")
    return descriptor.table.row_identity, descriptor.table.index_field, numeric[0]


left_identity, left_index, left_value = analysis_contract(left_descriptor)
right_identity, right_index, right_value = analysis_contract(right_descriptor)
if left_identity != right_identity:
    raise ValueError("results do not share field, namespace, and version identity")

for descriptor in (left_descriptor, right_descriptor):
    provenance = descriptor.provenance
    print(
        provenance.metric,
        provenance.plugin.name,
        provenance.plugin.version,
        provenance.catalog_fingerprint,
        provenance.created_at,
    )

left = client.result_dataframe(left_ref)[[left_index, left_value]].rename(
    columns={left_index: "row_id", left_value: "left_value"}
)
right = client.result_dataframe(right_ref)[[right_index, right_value]].rename(
    columns={right_index: "row_id", right_value: "right_value"}
)
joined = left.merge(right, on="row_id", how="inner", validate="one_to_one")
print(joined["left_value"].corr(joined["right_value"]))
```

If several numeric columns exist, select using their names, descriptions,
units, and nullability; consult `provenance.output` for batch source metadata.
The merge and correlation execute in the external Python process.

## Related Documentation

- [Job API](../job-api/) documents authentication and descriptor routes.
- [Metrics Catalog](../metrics-catalog/) explains output declarations.
- [lyra-api](../lyra-api/) lists Python client methods.
- [Deployment](../deployment/) covers public URL and limit configuration.
