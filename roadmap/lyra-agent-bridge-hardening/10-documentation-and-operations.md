# Document the Hardened Agent Workflow

## Goal

Document the final agent, operator, and external-analysis workflow without
describing removed compatibility behavior.

## Background from the discussion

The existing documentation reflects the handwritten transport, MCP-only token,
public job downloads, non-deduplicating idempotency key, relative handoff, and
smaller descriptor contract.

## Scope

- Update setup for the official MCP transport and `LYRA_AGENT_API_KEY`.
- Document the exact public/protected/admin route boundary.
- Document strict tools, lookup, search, polling, provenance, idempotency, rate
  limits, absolute downloads, and result expiry.
- Update the two-metric external correlation example to use descriptor-declared
  columns and row identity.
- Add operator notes for public base URL and submission-limit configuration.
- Keep README and all related reference pages consistent.

## Out of scope

- Document removed names, migration instructions, or compatibility shims.
- Promise OAuth, durable results, Parquet, cancellation, or server-side analysis.
- Add implementation code.

## Files or areas likely affected

- `README.md`
- `docs/src/content/docs/ai-agent-guide.md`
- `docs/src/content/docs/deployment.md`
- `docs/src/content/docs/job-api.md`
- `docs/src/content/docs/lyra-api.md`
- `docs/src/content/docs/mcp-agent-bridge.md`
- `docs/src/content/docs/metrics-catalog.md`

## Required behavior

- Configuration examples use only current setting and environment-variable
  names.
- Route tables clearly distinguish public, agent-authenticated, and admin-only
  access.
- The workflow starts with location lookup and metric search, uses idempotency,
  polls one result reference, inspects provenance, and downloads JSONL.
- The analysis example validates shared row identity and uses declared numeric
  columns rather than hard-coded assumptions.
- Documentation states the 10-per-60-second defaults, retry behavior, and
  configurable nature of the limit.
- Documentation states that results expire and external code performs analysis.

## Implementation notes

- Use examples aligned with actual tool and client model names after prior
  steps.
- Remove stale text rather than preserving old and new alternatives.
- Never include real credentials or suggest using the admin key for agents.

## Tests and verification

- Build the Astro documentation using the repository's existing package script.
- Search documentation and examples for removed `LYRA_MCP_API_KEY` references,
  public result claims, and statements that idempotency does not deduplicate.
- Use final repository validation for Python examples and config regressions.

## Step exit checklist

- [ ] README and all agent/API/deployment pages describe one current workflow.
- [ ] Removed credential and behavior references are absent.
- [ ] Auth, provenance, idempotency, limits, expiry, and downloads are explicit.
- [ ] The external correlation example uses descriptor metadata safely.
- [ ] The documentation build succeeds.

## Decision gate before the next step

Proceed to final validation only when the built documentation has no stale
configuration or security-boundary guidance.

## Next-step context

Run the schema-version-6 final validation contract and its end-to-end scenarios.
