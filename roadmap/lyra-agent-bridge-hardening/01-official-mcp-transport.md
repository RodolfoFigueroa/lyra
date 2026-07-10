# Adopt the Official MCP Transport

## Goal

Replace Lyra's handwritten JSON-RPC transport with the stable official Python
MCP SDK while preserving the current metric backend behavior.

## Background from the discussion

The current FastAPI endpoint handles the happy path but does not fully implement
Streamable HTTP lifecycle, GET, Origin, protocol-version, notification, and
content-negotiation rules. Later security and tool-contract work should build on
the official transport rather than duplicate protocol code.

## Scope

- Add and lock the stable official Python MCP SDK dependency below its next
  prerelease major line.
- Register Lyra's existing stable tools through the SDK.
- Mount the SDK Streamable HTTP application at the configured MCP path with its
  required lifespan handling.
- Configure stateless JSON responses unless SDK conformance requires sessions.
- Enforce the SDK's transport security behavior, including safe Origin handling.
- Test through the official SDK client, not only direct JSON dictionaries.

## Out of scope

- Rename the agent credential or protect REST job routes.
- Change metric selection, execution, polling, or result semantics.
- Add new tools or descriptor fields.

## Files or areas likely affected

- `packages/lyra_mcp/pyproject.toml`
- `packages/lyra_mcp/src/lyra/mcp/server.py`
- `packages/lyra_mcp/src/lyra/mcp/__init__.py`
- `lyra_app/main.py`
- `tests/test_mcp_server.py`
- `tests/test_main_lifespan.py`
- `uv.lock`

## Required behavior

- An official SDK client can initialize, list tools, call a tool, and close
  cleanly against the mounted `/mcp` endpoint.
- The endpoint follows official Streamable HTTP GET, POST, notification,
  protocol-version, content-type, and lifecycle behavior.
- Disallowed Origins are rejected and normal non-browser agent clients remain
  usable.
- MCP server startup and shutdown do not leak session-manager tasks.
- Existing Lyra tool payloads and structured errors remain functionally intact.
- The custom discovery GET response and handwritten JSON-RPC dispatcher are
  removed rather than retained as compatibility paths.

## Implementation notes

- Keep Lyra backend calls separate from SDK transport registration so later
  steps can type and test tools without transport internals.
- Pin the stable SDK major line explicitly; do not select an alpha or beta.
- Prefer SDK-supported ASGI composition and lifespan APIs over custom protocol
  adapters.

## Tests and verification

- Use the manifest-declared transport tests.
- Cover official-client initialization and tool invocation, invalid bearer
  credentials, invalid Origin, protocol-version handling, accepted
  notifications, and clean lifespan shutdown.

## Step exit checklist

- [ ] The official MCP SDK owns protocol and Streamable HTTP handling.
- [ ] A real SDK client passes initialization and tool-call tests.
- [ ] The old custom dispatcher and discovery response are gone.
- [ ] Transport security and lifespan regressions are covered.

## Decision gate before the next step

Proceed only when the official client integration passes without a custom
protocol fallback.

## Next-step context

The next step replaces the MCP-only secret with the unified agent credential
and applies it to all job and result routes.
