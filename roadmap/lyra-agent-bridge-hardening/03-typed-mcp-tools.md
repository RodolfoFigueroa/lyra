# Define Strict MCP Tool Contracts

## Goal

Give every MCP tool strict, machine-readable input and output contracts with
consistent error behavior.

## Background from the discussion

Current tool definitions advertise JSON Schema but manually validate only a
subset of it. Unknown arguments may be ignored, non-finite wait values can
escape validation, and structured results have no declared output schemas.

## Scope

- Define strict typed input and output models for every MCP tool.
- Let the official SDK publish and enforce input and output schemas.
- Reject unknown properties, invalid result references, invalid ranges, and
  non-finite numbers.
- Preserve structured content plus compact serialized text for compatible MCP
  clients.
- Add accurate read-only, idempotent, destructive, and open-world annotations.
- Separate transport registration, tool services, and contract models.

## Out of scope

- Add new discovery tools or result provenance fields.
- Change metric-run spatial support.
- Add server-side statistical analysis.

## Files or areas likely affected

- `packages/lyra_mcp/src/lyra/mcp/server.py`
- `packages/lyra_mcp/src/lyra/mcp/models.py`
- `packages/lyra_mcp/src/lyra/mcp/tools.py`
- `packages/lyra_mcp/src/lyra/mcp/__init__.py`
- `tests/test_mcp_server.py`

## Required behavior

- Every listed tool includes an input schema and output schema matching its
  structured result.
- Search, inspection, polling, preview, metadata, and download-handoff tools are
  annotated read-only and idempotent; metric execution is not idempotent until
  an idempotency key is supplied in a later step.
- Extra fields and invalid types produce protocol-appropriate invalid-argument
  errors; domain failures remain visible structured tool errors.
- `NaN`, infinities, negative waits, and waits above the declared maximum are
  rejected without entering a poll loop.
- Raw table rows remain outside normal MCP responses.

## Implementation notes

- Prefer Pydantic models and SDK schema generation over parallel handwritten
  schema dictionaries.
- Keep reusable domain errors independent of MCP transport exceptions.
- Do not weaken strict SDK models to accommodate malformed callers.

## Tests and verification

- Use the manifest-declared MCP tests.
- Validate listed schemas, successful structured output, extra arguments,
  boundary values, non-finite values, malformed result references, unknown
  tools, and domain-error visibility through the official client.

## Step exit checklist

- [ ] All tools have enforced input and output schemas.
- [ ] Tool annotations reflect actual side effects and idempotency.
- [ ] Non-finite and out-of-range wait values are rejected.
- [ ] Transport, contracts, and domain logic are separated cleanly.

## Decision gate before the next step

Proceed only when SDK-client schema and invalid-input tests pass.

## Next-step context

The next step persists immutable run context needed to make terminal result
references reproducible.
