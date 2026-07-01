# Client And State

## Goal

Build the reusable TUI configuration, client adapter, polling state, error handling, and shared widgets that all screens will use.

## Background From The Discussion

The TUI should use `lyra-api` as the only integration boundary. It should handle offline API, missing admin auth, unavailable Redis, and Celery inspect uncertainty gracefully. It should not import `lyra_app` internals.

## Scope

- Add a small runtime configuration model for host, scheme, admin key, timeout, and refresh interval.
- Add an API adapter around `AsyncLyraAPIClient` or `LyraAPIClient`.
- Add a central snapshot/state object for the latest fetched admin data.
- Add background polling for read-only status data.
- Add a consistent error model for authentication errors, connection failures, API errors, and partial data.
- Add shared UI elements for connection status, loading states, empty states, and action result messages.

## Out Of Scope

- No mutating admin workflows yet.
- No detailed screen layouts beyond simple state display.
- No server API changes.
- No direct Redis, Celery, Docker, or filesystem access.

## Files Or Areas Likely Affected

- `packages/lyra_tui/src/lyra/tui/config.py`
- `packages/lyra_tui/src/lyra/tui/client.py`
- `packages/lyra_tui/src/lyra/tui/state.py`
- `packages/lyra_tui/src/lyra/tui/app.py`
- `packages/lyra_tui/src/lyra/tui/widgets/`
- `tests/` or `packages/lyra_tui/tests/`

## Required Behavior

- The app should fetch public health without requiring admin auth.
- The app should fetch admin status only when an admin key is configured.
- API connection failures should be displayed in the TUI instead of crashing it.
- Missing or invalid admin auth should produce a clear locked/error state.
- Polling should be cancelable when the app exits.
- Polling should not block the UI.
- One failed fetch should not poison future refresh attempts.

## Implementation Notes

- Textual workers or timers are the preferred way to run polling without blocking the UI.
- Start with a conservative refresh interval, such as 2 to 5 seconds.
- Keep API adapter methods thin and typed around existing `lyra-api` methods:
  - `get_health`
  - `get_admin_status`
  - `get_admin_config_summary`
  - `get_admin_catalog`
  - `get_admin_workers`
  - `get_admin_queues`
  - `list_admin_jobs`
  - `list_plugin_repos`
  - `list_plugin_routing`
- Prefer behavioral tests around state transitions over pixel/layout tests.
- If using the async client causes friction because it creates a session per request, keep it anyway for MVP and document a later shared-session optimization.

## Tests And Verification

- Unit tests with fake client responses for:
  - successful snapshot refresh
  - public health success plus admin auth failure
  - connection failure recovery on later success
  - polling cancellation on app shutdown, if testable without brittle timing
- Textual pilot tests for:
  - app starts in disconnected/loading state
  - injected fake state appears in the status area
- Validation commands:
  - `uv run pytest`
  - `uv run ruff format`
  - `uv run ruff check --fix`
  - `uv run ty check --fix`

## Completion Criteria

- The TUI has a stable internal state model.
- API failures are visible and recoverable.
- Screens can subscribe to or read the latest snapshot without making their own duplicate client setup.
- Tests cover the adapter/state behavior with fake data.

## Handoff Notes For The Next Step

The next step should implement read-mostly dashboard, jobs, workers, and queues views using this shared state. Keep mutating actions disabled or stubbed until the action workflow step.

