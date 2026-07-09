# Document Agent Bridge And Developer Analysis Flow

## Goal

Document how operators expose Lyra MCP, how agents should use the stable tools,
and how developers retrieve raw data for local analysis.

## Background from the discussion

The bridge should be understandable to Codex and to developers building their
own agent clients. The documentation must make the boundary clear: Lyra runs
metrics and exposes results; clients perform arbitrary analysis.

## Scope

- Add a dedicated MCP agent bridge documentation page.
- Update the AI agent guide with the stable MCP workflow.
- Update job and client docs for result refs, descriptors, and JSONL exports.
- Include a developer-side correlation example using two result refs.

## Out of scope

- Marketing pages.
- Documentation for dynamic per-metric tools.
- Documentation for raw GeoJSON or census-tract-list MCP inputs.

## Files or areas likely affected

- `docs/src/content/docs/mcp-agent-bridge.md`
- `docs/src/content/docs/ai-agent-guide.md`
- `docs/src/content/docs/job-api.md`
- `docs/src/content/docs/lyra-api.md`
- `docs/src/content/docs/python-client.md`
- `docs/src/content.config.ts`
- `README.md`
- `tests/test_api_client_jobs.py`
- `tests/test_mcp_server.py`

## Required behavior

- Docs show Codex-style MCP configuration against
  `https://lyra.mydomain.com/mcp`.
- Docs describe bearer-token setup without exposing admin APIs.
- Docs list all stable MCP tools and their intended sequencing.
- Docs explain `running` responses and polling with `lyra_get_job_result`.
- Docs show raw met-zone-code-only input for v1.
- Docs show local correlation analysis using downloaded result tables.
- Docs explicitly say Lyra does not provide SQL or statistical analysis tools in
  this feature.

## Implementation notes

- Keep examples aligned with actual SDK/client method names.
- Use fake metric names or smoke-style examples unless real plugin metrics are
  available in tests.
- Avoid implying result refs are durable beyond the Redis TTL.
- Mention JSONL as the required v1 raw export format and Parquet as future
  potential only if useful.

## Tests and verification

- Keep client and MCP tests aligned with documented examples.
- If docs navigation is schema-validated, update docs config and run the
  relevant docs build/check manually if available.

## Step exit checklist

- A new docs page explains the full operator, agent, and developer workflow.
- Existing API docs include descriptor and raw export routes.
- Python client docs include result-ref helpers.
- The README points users to the MCP bridge docs if appropriate.

## Decision gate before the next step

Confirm the documented workflow matches the shipped tool descriptions and
client helper names.

## Next-step context

Final validation should verify the complete roadmap behavior across contracts,
HTTP routes, clients, MCP tools, and docs.
