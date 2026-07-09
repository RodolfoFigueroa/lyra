---
title: MCP Agent Bridge
description: Expose Lyra metrics to Codex and other MCP clients with stable tools, result references, and JSONL handoffs.
---

Lyra's MCP bridge lets agents discover public metric contracts, run one metric
for a metropolitan zone, poll job results, and hand raw result access back to
developer tools. Lyra runs metrics and stores results; the MCP client or a local
developer script performs arbitrary analysis.

The v1 MCP surface is intentionally small. It does not expose admin plugin,
worker, queue, server-management, SQL, or statistical analysis tools.

## Operator Setup

Enable the Lyra API with MCP mounted at `/mcp`, then configure a dedicated MCP
Bearer token through `LYRA_MCP_API_KEY`. Keep this separate from
`LYRA_ADMIN_API_KEY`; MCP clients should not need admin API credentials.

Codex can connect to the streamable HTTP MCP server with a `config.toml` entry:

```toml
[mcp_servers.lyra]
url = "https://lyra.mydomain.com/mcp"
bearer_token_env_var = "LYRA_MCP_API_KEY"
```

Set `LYRA_MCP_API_KEY` in the environment that starts Codex. Codex sends it as
an `Authorization: Bearer ...` header when calling `https://lyra.mydomain.com/mcp`.

For local development, the same shape works with a local URL:

```toml
[mcp_servers.lyra_local]
url = "http://localhost:5219/mcp"
bearer_token_env_var = "LYRA_MCP_API_KEY"
```

## Stable Tools

Use the tools in this sequence:

| Tool | Use |
| --- | --- |
| `lyra_search_metrics` | Search the public catalog by words from the user's request. |
| `lyra_get_metric` | Inspect one metric's request schema, spatial inputs, and output declaration before running it. |
| `lyra_run_metric` | Start one metric for a raw metropolitan zone code. |
| `lyra_get_job_result` | Continue polling a returned `lyra://results/{job_id}` reference until the job is terminal. |
| `lyra_get_result_metadata` | Read compact descriptor metadata, lifetime, table/file shape, summary, and errors. |
| `lyra_get_result_preview` | Read preview rows and summary without raw table hydration. |
| `lyra_download_result` | Get authenticated Lyra API handoff metadata for JSONL table export. |

MCP v1 exposes stable tools rather than dynamic per-metric tools. Search first,
inspect the selected metric, then run it.

## Agent Run Flow

First search for a metric:

```json
{
  "query": "clinic accessibility",
  "limit": 5
}
```

Then inspect the chosen metric:

```json
{
  "metric": "smoke_accessibility_metric"
}
```

Run the metric with a raw metropolitan zone code. The MCP bridge owns the
spatial wrapper and inserts the correct metric spatial field, so do not pass raw
GeoJSON, census tract lists, or the metric's spatial field inside `parameters`.

```json
{
  "metric": "smoke_accessibility_metric",
  "met_zone_code": "09.01",
  "parameters": {
    "year": 2025
  },
  "wait_seconds": 2
}
```

If the job is still active, `lyra_run_metric` returns a continuation payload:

```json
{
  "status": "running",
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "poll_after_seconds": 1,
  "next_tool": "lyra_get_job_result"
}
```

Do not rerun the metric for a `running` response. Wait at least
`poll_after_seconds`, then call the returned `next_tool`:

```json
{
  "result_ref": "lyra://results/job-1",
  "wait_seconds": 30
}
```

Terminal responses are compact result descriptors. They include status,
`result_kind`, `result_ref`, lifetime information, summary, preview rows, and
raw-access metadata. Result references are valid only while Redis retains the
job result key; expired references must be rerun if the user still needs data.

## Raw Result Handoff

Use `lyra_get_result_preview` when a small sample is enough for the agent's
answer. Use `lyra_get_result_metadata` when the agent needs shape, lifetime, or
summary metadata.

For local analysis, call `lyra_download_result` on a table result reference. It
does not inline raw rows in the MCP response. It returns the Lyra HTTP route and
Python helper names for downloading JSONL:

```json
{
  "result_ref": "lyra://results/job-1"
}
```

The handoff points to `GET /jobs/{job_id}/result/table.jsonl` and the
`LyraAPIClient.download_result(result_ref, path, format="jsonl")` helper. JSONL
is the required v1 raw table export format.

## Developer Correlation Example

After an agent produces two table result references, download or hydrate both
tables locally with `lyra-api` and compute the correlation in Python:

```python
from lyra.api import LyraAPIClient

client = LyraAPIClient("lyra.mydomain.com", secure=True)

access_ref = "lyra://results/job-access"
population_ref = "lyra://results/job-population"

access = client.result_dataframe(access_ref)
population = client.result_dataframe(population_ref)

joined = access.merge(population, on="_result_index", suffixes=("_access", "_pop"))
correlation = joined["accessibility_score"].corr(joined["population_count"])

print(correlation)
```

The column names above are examples. Use columns declared by the selected
metrics and returned in each descriptor's table metadata. The correlation runs
in your Python process after the raw JSONL tables are retrieved; Lyra does not
provide SQL execution or statistical analysis tools for this feature.

If pandas is not available, download JSONL files directly:

```python
client.download_result(access_ref, "access.jsonl", format="jsonl")
client.download_result(population_ref, "population.jsonl", format="jsonl")
```

## Related HTTP And Client Docs

- [Job API](../job-api/) describes descriptor, JSONL, and download routes.
- [Python Client](../python-client/) shows the common client workflow.
- [lyra-api](../lyra-api/) lists result-reference helper methods.
