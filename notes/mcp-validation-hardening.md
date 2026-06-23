# MCP Validation And Hardening

## Objective

Prove that the first MCP version works in the real deployment shape before
adding richer features. This phase should catch integration problems, stale
schema behavior, and agent misuse patterns.

## Client Validation

Test with:

- Tavi.
- At least one generic MCP client.
- A small scripted MCP smoke test.

The smoke test should verify:

- Initialize/connect.
- List tools.
- List resources.
- Describe one metric.
- Submit one small metric job.
- Retrieve the result.
- Handle one validation error.

## Pangolin And Proxy Validation

Confirm that the reverse proxy preserves behavior needed by MCP:

- POST requests to the MCP endpoint.
- Required auth headers.
- `Accept` headers.
- `Origin` headers.
- Long-lived HTTP responses.
- SSE behavior if used.
- Request and response body size limits.
- Timeouts long enough for agent workflows.

## Deployment Validation

Verify behavior in the same topology used by normal deployments:

- API container.
- Celery worker container.
- Redis.
- Plugin volume.
- Pangolin/OAuth.

Avoid only testing MCP on a local direct connection if production clients will
always pass through the proxy.

## Reload Validation

Exercise plugin update behavior:

1. Start the server.
2. List MCP tools/resources.
3. Update or reload plugins.
4. Confirm the registry refreshes.
5. Confirm MCP tool/resource lists refresh.
6. Confirm stale metric names produce clear errors.
7. Confirm active jobs are handled according to the existing worker restart
   policy.

## Failure Validation

Test expected failures:

- Unknown metric.
- Invalid payload.
- Redis unavailable.
- Worker unavailable.
- Worker exception.
- Cancelled task.
- Expired result.
- Missing result file.
- Oversized payload.

Each failure should produce a clear machine-readable error type and a concise
message useful to an agent.

## Guardrails

Even with vetted plugins and institutional OAuth, keep basic operational
guardrails:

- Per-user or per-client rate limits.
- Queue limits.
- Payload size limits.
- Result size limits or handle-only behavior.
- Job timeout policy.
- Result TTL.
- Audit logs.
- Cost and latency metadata exposed to agents.

The main risk is not only malicious use. It is also authorized agents launching
expensive or inappropriate jobs by mistake.

## Metadata Quality Review

Review every exposed metric for:

- Clear agent-facing description.
- Correct input examples.
- Correct output schema.
- Units.
- Valid geography.
- Temporal coverage.
- Known limitations.
- Data source notes.
- Cost and latency class.

This should be treated as part of release quality, not documentation polish.

## Exit Criteria

Validation and hardening is complete when:

- Tavi can use MCP end to end.
- A generic MCP client can use MCP end to end.
- The MCP endpoint works through Pangolin.
- Common failures are predictable.
- Plugin reloads do not leave clients permanently confused.
- Basic queue, payload, result, and audit guardrails are in place.
- The team has reviewed metadata quality for all initially exposed indicators.

