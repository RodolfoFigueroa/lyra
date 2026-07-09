# Scaffold First-Party Lyra MCP Server

## Goal

Create a first-party Lyra MCP package and authenticated `/mcp` server surface
without implementing all tools yet.

## Background from the discussion

Codex and other agents should connect to `https://lyra.example.com/mcp` as a
Streamable HTTP MCP server. The first version should use bearer token auth and
avoid exposing admin operations.

## Scope

- Add a workspace package for Lyra MCP support.
- Add minimal MCP server startup and health/discovery behavior.
- Add bearer-token configuration separate from admin API auth.
- Mount or expose the MCP server at `/mcp` when enabled.
- Add tests for configuration, auth, and server availability.

## Out of scope

- OAuth.
- Admin plugin management tools.
- Dynamic per-metric tools.
- Full metric run/result tools; those come in later steps.

## Files or areas likely affected

- `pyproject.toml`
- `uv.lock`
- `packages/lyra_mcp/pyproject.toml`
- `packages/lyra_mcp/src/lyra/mcp`
- `lyra_app/config.py`
- `lyra_app/main.py`
- `lyra_app/auth.py`
- `tests/test_config_contract.py`
- `tests/test_main_lifespan.py`
- `tests/test_mcp_server.py`

## Required behavior

- The MCP package can be imported and tested as part of the uv workspace.
- The server rejects unauthenticated requests when MCP is enabled.
- MCP auth is controlled by a dedicated secret such as `LYRA_MCP_API_KEY`.
- MCP instructions explain stable tools, met-zone-only input, result refs, and
  the poll-on-running workflow.
- Admin routes are not exposed as MCP tools.

## Implementation notes

- Use the official Python MCP server package if appropriate.
- If adding a dependency is necessary, use `uv add` rather than editing lockfile
  state by hand.
- Keep the MCP bridge using `lyra-api` client primitives where possible.
- Avoid coupling the MCP server to private registry functions if HTTP client
  calls are sufficient.

## Tests and verification

- Add tests for enabled/disabled config behavior.
- Add tests that unauthenticated MCP requests fail.
- Add tests that authenticated initialization returns server instructions.

## Step exit checklist

- `packages/lyra_mcp` exists and is part of the workspace.
- MCP configuration is documented in config tests.
- `/mcp` can be initialized in tests with bearer auth.
- No metric execution tools are exposed yet.

## Decision gate before the next step

Confirm the package and mount architecture are acceptable before adding tool
logic on top.

## Next-step context

The next step will implement metric search, metric inspection, and met-zone
metric execution tools.
