# Docs And Polish

## Goal

Make the TUI discoverable, documented, and comfortable to run in local development without changing Lyra's server runtime shape.

## Background From The Discussion

The TUI is a first-party operator tool, but it should remain separate from the API/worker runtime dependencies. Documentation should explain how to run it against a live Lyra instance and what it can manage.

## Scope

- Add TUI usage docs to the project documentation.
- Add README or package-level usage notes if that is the repo convention.
- Document required admin auth and environment variables.
- Document local and Compose-backed validation startup.
- Polish CLI help text and error messages.
- Confirm packaging metadata and console script behavior.
- Add changelog/release notes only if the repo already has a convention for them.

## Out Of Scope

- No feature expansion beyond the agreed MVP.
- No separate docs site redesign.
- No Docker image or Compose service for the TUI unless explicitly requested later.
- No screenshots required for terminal UI docs unless the project adopts them.

## Files Or Areas Likely Affected

- `docs/src/content/docs/operations.md`
- `docs/src/content/docs/local-development.md`
- `docs/src/content/docs/reference.md`, if command references belong there
- `README.md`, if top-level quick start should mention the TUI
- `packages/lyra_tui/pyproject.toml`
- `packages/lyra_tui/src/lyra/tui/__main__.py`
- Tests for CLI help or package metadata if practical

## Required Behavior

- Docs should show how to install/sync the workspace and launch the TUI.
- Docs should explain host/security/admin-key options.
- Docs should state that the TUI connects to a running Lyra API and does not start services itself.
- Docs should mention that mutating admin actions require Bearer auth.
- CLI help should be enough to run against the default local dev server.
- The TUI package should remain excluded from the API Docker runtime dependency set.

## Implementation Notes

- Prefer concise docs with commands:
  - `uv sync`
  - `uv run lyra-tui --host localhost:5219 --no-secure`
  - `LYRA_ADMIN_API_KEY=... uv run lyra-tui --host localhost:5219 --no-secure`
- In docs, recommend `docker compose -f docker/docker-compose-dev.yml up --build` for a full local stack before running the TUI.
- Include a short troubleshooting section for:
  - API offline
  - missing admin key
  - Redis unavailable
  - worker inspect unavailable
  - queue pending depth unknown
- Keep operator docs focused on behavior, not implementation internals.

## Tests And Verification

- Build docs if the docs changes are non-trivial:
  - `npm run build --prefix docs`
- Verify CLI help:
  - `uv run lyra-tui --help`
- Verify package can be imported:
  - `uv run python -m lyra.tui --help`
- Repository checks:
  - `uv run pytest`
  - `uv run ruff format`
  - `uv run ruff check --fix`
  - `uv run ty check --fix`

## Completion Criteria

- Operators can discover and run the TUI from docs.
- CLI help and docs agree on defaults and auth behavior.
- The TUI package is still dependency-isolated from the server runtime.
- Docs build if touched.

## Handoff Notes For The Next Step

Proceed to the final validation pass. Use a live Lyra stack for end-to-end checks and clean up every service started for validation.

