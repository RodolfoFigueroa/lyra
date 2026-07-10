# Enforce the Agent API Boundary

## Goal

Introduce one dedicated agent credential and consistently protect MCP plus the
entire job and result lifecycle.

## Background from the discussion

The current MCP endpoint requires `LYRA_MCP_API_KEY`, but callers can bypass it
through unauthenticated REST job and result routes. Download metadata also
claims authentication even though the raw route is public.

## Scope

- Replace `LYRA_MCP_API_KEY` with `LYRA_AGENT_API_KEY` without a compatibility
  alias.
- Centralize constant-time Bearer validation for agent-facing protected routes.
- Protect job creation, status, events, terminal results, descriptors, JSONL,
  and file downloads.
- Keep health, metrics, data types, and metropolitan-zone lookup public.
- Keep admin authentication separate and non-interchangeable.
- Add `agent_api_key` support to synchronous and asynchronous `lyra-api`
  clients.

## Out of scope

- OAuth, multiple principals, per-job ownership, and token rotation APIs.
- Rate limiting and idempotent job creation.
- Absolute result download URLs.

## Files or areas likely affected

- `config.example.toml`
- `lyra_app/agent_auth.py`
- `lyra_app/config.py`
- `lyra_app/main.py`
- `lyra_app/routes/jobs.py`
- `packages/lyra_api/src/lyra/api/client/`
- `packages/lyra_mcp/src/lyra/mcp/server.py`
- Configuration, route, client, and MCP tests.

## Required behavior

- Application startup requires a non-empty `LYRA_AGENT_API_KEY` whenever agent
  job access or MCP is enabled by the runtime configuration.
- The same agent Bearer token authorizes MCP and every `/jobs` lifecycle route.
- Missing, malformed, and invalid agent credentials return consistent `401` or
  `403` responses without disclosing secret material.
- The admin token cannot authorize agent routes, and the agent token cannot
  authorize admin routes.
- Public catalog, health, data-type, and location lookup routes remain public.
- Both Python clients attach the agent credential to protected job and result
  calls while retaining the separate admin credential for admin calls.
- Secret-free config summaries and logs never expose the credential.

## Implementation notes

- Remove old names and branches outright; compatibility is not required.
- Use one reusable dependency or middleware rather than duplicating token
  comparisons across job routes.
- Ensure in-process MCP backend calls do not accidentally require a second auth
  pass after the MCP request has already been authenticated.

## Tests and verification

- Use the manifest-declared auth tests.
- Exercise the full public/protected route matrix with missing, agent, admin,
  and invalid tokens.
- Verify sync and async clients send the correct credential and downloads work
  only when authenticated.

## Step exit checklist

- [ ] `LYRA_AGENT_API_KEY` is the only agent credential name.
- [ ] Every job and result route is protected.
- [ ] Public metadata and lookup routes remain public.
- [ ] Agent and admin credentials are not interchangeable.
- [ ] Sync and async clients cover the protected workflow.

## Decision gate before the next step

Proceed only when route-matrix tests demonstrate there is no unauthenticated
job execution or result access path.

## Next-step context

The next step defines strict SDK-native tool schemas on top of the authenticated
official MCP transport.
