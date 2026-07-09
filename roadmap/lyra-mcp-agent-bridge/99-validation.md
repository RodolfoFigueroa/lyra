# Final Validation

## Goal

Verify that Lyra exposes a stable, authenticated MCP bridge over its metric
catalog and job/result APIs while preserving the agreed boundary between metric
execution and downstream analysis.

## Implementation step checklist

- `01-agent-metric-catalog-contract.md`: metric catalog exposes spatial input
  metadata and derived search fields without plugin-authored metadata.
- `02-result-descriptor-contract.md`: SDK descriptor models, preview builders,
  summary builders, and Redis TTL lifetime helpers are present and tested.
- `03-result-access-http-api.md`: public descriptor and JSONL raw export routes
  are present, documented, and covered by route tests.
- `04-lyra-api-result-client.md`: sync and async client helpers accept result
  refs and raw job ids, fetch descriptors, and download JSONL.
- `05-mcp-server-scaffold-and-auth.md`: first-party MCP package exists, is
  authenticated separately from admin APIs, and can be mounted or served at
  `/mcp`.
- `06-mcp-search-and-run-tools.md`: stable search, inspect, and met-zone run
  tools work without dynamic per-metric tool exposure.
- `07-mcp-result-tools.md`: result polling, metadata, preview, and raw-access
  tools work with compact responses and structured errors.
- `08-documentation-and-examples.md`: operator, agent, and developer docs match
  the implemented contracts and include the local-analysis boundary.

## Repository-wide validation commands

The manifest-managed validation suite runs Roadmap Runner lint for this roadmap,
ruff format checking, ruff linting, type checking, and the full pytest suite.
If docs tooling is available in the implementation environment, also run the
docs check or build command and record the result in the final validation notes.

## End-to-end scenarios

- Configure the MCP server with bearer auth and verify unauthenticated calls are
  rejected.
- Search for a metric through MCP, inspect its schema, run it with a raw
  metropolitan zone code, and receive either a terminal descriptor or a running
  response with `next_tool`.
- Continue a running result with `lyra_get_job_result` until terminal.
- Fetch descriptor metadata and preview without inlining full raw rows.
- Download JSONL through the client helper and compute a local statistic outside
  Lyra.
- Confirm expired result refs produce a structured error that does not pretend
  data is still available.

## Regression checks

- Existing `/jobs/{job_id}/result` and file download routes remain compatible.
- Existing plugin runner return models remain compatible.
- Catalog fingerprint changes only for public contract changes.
- Admin plugin and worker operations are not exposed through MCP tools.
- No SQL, correlation, regression, or general statistics APIs are added to Lyra.
- MCP v1 rejects raw GeoJSON and census-tract-list location inputs.

## Services and cleanup

Use local test services already expected by the Lyra test suite. Stop any API,
worker, Redis, or Docker Compose services started manually during validation.
Do not leave long-running MCP or worker processes active after validation.

## Clear pass/fail criteria

Pass when all manifest-managed validation commands succeed, the end-to-end MCP
scenarios work with compact descriptors and raw JSONL access, and docs reflect
the implemented contracts. Fail if the bridge exposes admin operations by
default, requires dynamic per-metric tools, returns large tables inline by
default, omits raw result access, or adds server-side SQL/statistical analysis
features.
