# Lyra MCP Roadmap

## Purpose

Lyra currently exposes indicators through a REST and WebSocket API. The
indicator implementations live in external plugin repositories and are loaded
into the executor at startup. The current agent-facing integration is the
`/metrics` endpoint plus short plugin-provided `TAVI_HINT` text.

The goal of the MCP work is to add a standard agent-facing interface that can
serve Tavi now and arbitrary MCP-capable agents later. MCP should be an adapter
over Lyra's existing registry, queue, and result download flow. It should not
replace the existing REST and WebSocket API.

## Guiding Decisions

- Keep the REST and WebSocket API as the stable application/client API.
- Add a central MCP endpoint to the executor, probably `/mcp`.
- Generate MCP-facing descriptions and tools from the existing plugin registry.
- Treat plugin metadata as the primary design surface for good agent behavior.
- Keep execution backed by the existing Celery and Redis workflow.
- Return handles or resource links for long-running, large, and file outputs.
- Maintain simple fallback tools even if newer MCP task support is adopted.

## Phase Overview

1. Preparation
   Define structured contracts before implementing protocol plumbing.

2. MCP First Version
   Add a minimal, robust MCP adapter alongside the current API.

3. Validation And Hardening
   Test with real clients and deployment infrastructure before extending scope.

4. Extension
   Add richer MCP and agent affordances after the first version is proven.

## Reference Documents

- [Preparation](mcp-preparation.md)
- [MCP First Version](mcp-first-version.md)
- [Validation And Hardening](mcp-validation-hardening.md)
- [Extension](mcp-extension.md)

## Major Risks To Track

- Agents choosing the wrong indicator because metadata is ambiguous.
- Agents running expensive jobs repeatedly or accidentally.
- MCP clients having uneven support for long-running tasks, progress, resources,
  or task cancellation.
- Plugin reloads causing stale MCP tool schemas in clients.
- Large result payloads being returned directly instead of as result handles.
- Pangolin or another reverse proxy interfering with MCP HTTP/SSE behavior.
- Metadata and implementation drifting apart as plugin repositories evolve.

## Done Means

The MCP work should be considered successful when an external MCP client can:

- Discover available Lyra indicators.
- Understand each indicator's purpose, inputs, outputs, units, limitations, and
  expected runtime.
- Submit a valid indicator request.
- Track or retrieve the result without relying on Lyra-specific hidden behavior.
- Recover from validation errors, worker failures, expired results, and plugin
  reloads in a predictable way.

