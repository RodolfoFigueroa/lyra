# Mutating Actions

## Goal

Add safe operator actions for job cancellation, worker restart, plugin repository management, catalog refresh, and metric routing.

## Background From The Discussion

The TUI should manage a running Lyra instance through existing admin routes. Mutating actions must require explicit confirmation because they can cancel work, restart workers, or change plugin state.

## Scope

- Add confirmation dialogs for all destructive or disruptive actions.
- Add job cancellation for active jobs.
- Add worker restart with configurable timeout.
- Add plugin repo add, enable/disable, delete, sync, and catalog refresh workflows.
- Add metric queue assignment and route deletion workflows.
- Refresh affected views after successful actions.
- Show action success/failure messages with enough detail for the operator to understand what happened.

## Out Of Scope

- No job submission wizard.
- No config file editing.
- No direct plugin source validation beyond API responses.
- No automatic worker restart after catalog refresh unless explicitly confirmed by the operator.
- No bulk destructive actions.

## Files Or Areas Likely Affected

- `packages/lyra_tui/src/lyra/tui/client.py`
- `packages/lyra_tui/src/lyra/tui/actions.py`, if a separate action service is useful
- `packages/lyra_tui/src/lyra/tui/screens/jobs.py`
- `packages/lyra_tui/src/lyra/tui/screens/workers.py`
- `packages/lyra_tui/src/lyra/tui/screens/plugins.py`
- `packages/lyra_tui/src/lyra/tui/screens/routing.py`
- `packages/lyra_tui/src/lyra/tui/widgets/dialogs.py`
- Tests for action workflows with fake clients

## Required Behavior

- Cancel job:
  - Available only when a selected job is active enough for cancellation to make sense.
  - Requires confirmation showing the job ID and status.
  - Handles `404`, `409`, and Redis unavailable errors gracefully.
- Restart workers:
  - Requires confirmation.
  - Lets the operator choose or edit timeout.
  - Shows the returned restart message.
- Refresh catalog:
  - Shows updated plugins, catalog fingerprint change, assigned queues, and whether restart is recommended.
  - If restart is recommended, offer a separate explicit restart action.
- Plugin repos:
  - Add requires source and optional ID.
  - Enable/disable should use the existing update route.
  - Delete requires confirmation with repo ID.
  - Sync should report changed/display name result.
- Routing:
  - Show allowed queues and default queue.
  - Assigning a metric should restrict queue choices to allowed queues when possible.
  - Deleting a route should clearly show whether it was actually deleted.

## Implementation Notes

- Keep all API calls inside the client/action layer so screens do not each implement HTTP error handling.
- Use Textual modal screens or dialogs for confirmations and forms.
- Disable action bindings when there is no valid selection.
- Treat API error text as operator-facing, but avoid dumping huge tracebacks into compact UI areas.
- After mutating actions, refresh the relevant snapshot and leave the operator on the same screen when possible.
- Preserve the HTTP-only boundary. Do not inspect local files or containers to infer state.

## Tests And Verification

- Unit tests with fake clients for each action:
  - success path
  - common API failure path
  - selection missing or invalid state
- Textual pilot tests for confirmation behavior:
  - cancel confirmation can be declined without calling the client
  - restart confirmation calls the client only after acceptance
  - plugin add form validates required source
- Validation commands:
  - `uv run pytest`
  - `uv run ruff format`
  - `uv run ruff check --fix`
  - `uv run ty check --fix`

## Completion Criteria

- The TUI can perform the core Lyra operator mutations safely through admin APIs.
- Every disruptive action requires confirmation.
- Successful actions update the UI state.
- Expected API failures are shown cleanly and do not crash the TUI.

## Handoff Notes For The Next Step

The next step should add documentation, packaging polish, and any final developer ergonomics needed before the end-to-end validation pass.

