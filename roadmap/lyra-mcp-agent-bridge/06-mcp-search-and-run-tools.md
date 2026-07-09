# Implement Metric Search And Met-Zone Run Tools

## Goal

Implement the core MCP discovery and execution tools for searching metrics,
inspecting a metric, and starting a metric run using a raw metropolitan zone
code.

## Background from the discussion

The model should not choose from hundreds of dynamic tools. It should search,
inspect, and call one stable run tool. `lyra_run_metric` should wait briefly
and return either a descriptor or a clear continuation handle.

## Scope

- Implement `lyra_search_metrics`.
- Implement `lyra_get_metric`.
- Implement `lyra_run_metric`.
- Use raw `met_zone_code` only.
- Translate met-zone input to the selected metric's spatial field.
- Merge non-spatial inputs from `parameters`.

## Out of scope

- Human-name met-zone lookup.
- `cvegeo_list` and raw GeoJSON support.
- Dynamic metric tools.
- Advanced semantic embeddings for search.

## Files or areas likely affected

- `packages/lyra_mcp/src/lyra/mcp`
- `packages/lyra_api/src/lyra/api/client`
- `packages/lyra_sdk/src/lyra/sdk/models/metric.py`
- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- `tests/test_mcp_server.py`
- `docs/src/content/docs/ai-agent-guide.md`

## Required behavior

- Search is lexical and derived from the public catalog.
- Search returns top candidates with concise reasons, required spatial fields,
  output kind, and relevant columns.
- `lyra_get_metric` returns the public metric contract.
- `lyra_run_metric` accepts `metric`, `met_zone_code`, `parameters`, and
  `wait_seconds`.
- `lyra_run_metric` waits up to `wait_seconds`; if incomplete, it returns
  `status="running"`, `job_id`, `result_ref`, `poll_after_seconds`, and
  `next_tool="lyra_get_job_result"`.
- If complete, it returns the descriptor envelope.
- Invalid parameters are surfaced as structured tool errors.

## Implementation notes

- Use the metric's `spatial_inputs` metadata instead of schema inference.
- For v1, reject metrics with multiple spatial fields unless a deterministic
  mapping is explicitly clear.
- Keep `wait_seconds` bounded server-side so tool calls cannot hang forever.
- Tool descriptions should tell agents not to rerun a metric when they receive
  a running result.

## Tests and verification

- Test search ranking over smoke or fake catalog entries.
- Test run payload translation for `location` and `bounds` spatial fields.
- Test immediate success, running timeout, unknown metric, invalid parameters,
  and unsupported spatial shapes.

## Step exit checklist

- Search and inspect tools work through MCP tests.
- Run tool returns deterministic success or running envelopes.
- Tool descriptions encode the continuation workflow.
- No dynamic per-metric tools are exposed.

## Decision gate before the next step

Confirm the run result envelope is understandable to agents before adding
dedicated result continuation and raw-access tools.

## Next-step context

The next step will implement result polling, preview, metadata, and raw download
tool behavior on top of the HTTP result data plane.
