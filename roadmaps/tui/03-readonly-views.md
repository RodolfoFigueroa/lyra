# Read-Only Views

## Goal

Implement the first useful operator views: dashboard, jobs, workers, queues, and catalog summaries.

## Background From The Discussion

The existing API is good enough for a TUI MVP. The first UI pass should focus on observability and selection before adding mutating actions. `/admin/queues` currently reports unknown pending depth, so the queue view must display that honestly.

## Scope

- Add a tabbed or routed Textual layout for read-only screens.
- Implement a dashboard showing API/Redis health, API version, metric count, worker count, default queue, job TTL, and catalog fingerprint.
- Implement a jobs table with recent jobs, status, metric, updated time, and selected-job details.
- Implement workers and queues views showing configured vs observed workers, worker status, task counts, queue consumers, assigned metric counts, and unknown queue depth markers.
- Implement catalog/plugin summary read-only display.
- Add keyboard navigation that is discoverable through Textual footer bindings.

## Out Of Scope

- No job cancellation yet.
- No worker restart yet.
- No plugin repo edits yet.
- No route assignment edits yet.
- No job submission workflow.

## Files Or Areas Likely Affected

- `packages/lyra_tui/src/lyra/tui/app.py`
- `packages/lyra_tui/src/lyra/tui/screens/dashboard.py`
- `packages/lyra_tui/src/lyra/tui/screens/jobs.py`
- `packages/lyra_tui/src/lyra/tui/screens/workers.py`
- `packages/lyra_tui/src/lyra/tui/screens/plugins.py`
- `packages/lyra_tui/src/lyra/tui/widgets/`
- `packages/lyra_tui/src/lyra/tui/styles.tcss`, if using a separate Textual CSS file
- Tests for screen rendering and table population

## Required Behavior

- The dashboard should remain useful with only public health data when admin auth is missing.
- The jobs screen should show empty state text when there are no recent jobs.
- Job status values should be visually distinguishable without relying only on color.
- Workers should show configured/offline, observed/online, and unknown inspect states.
- Queues should show `pending_depth_unknown` clearly instead of showing `0`.
- Long IDs, metric names, plugin source strings, and error messages should not break layout.
- Manual refresh should be available even when polling is enabled.

## Implementation Notes

- Use Textual `DataTable` for jobs, workers, queues, plugin repos, and routing summaries.
- Keep display mapping functions pure and testable, for example status labels and row builders.
- Use stable row keys for jobs and workers so refreshes update rows instead of rebuilding all UI where practical.
- Start with modest visual styling: dense, operational, scan-friendly. Avoid a decorative dashboard.
- Do not add in-app instructional prose. Let labels, bindings, and state messages carry the interface.

## Tests And Verification

- Unit tests for row-building and status-formatting helpers.
- Textual pilot tests for:
  - dashboard renders a fake healthy snapshot
  - jobs table renders multiple statuses and empty state
  - workers view renders online/offline/unknown states
  - queues view renders unknown pending depth distinctly
- Validation commands:
  - `uv run pytest`
  - `uv run ruff format`
  - `uv run ruff check --fix`
  - `uv run ty check --fix`

## Completion Criteria

- An operator can launch the TUI and understand instance health, recent jobs, worker state, queue assignments, plugin sources, and catalog summary without leaving the terminal.
- The UI handles empty, partial, and error data states.
- Tests cover core rendering behavior without requiring a live Lyra instance.

## Handoff Notes For The Next Step

The next step should add mutating actions. Reuse these views and selection models so actions operate on the selected job, worker, repo, or metric route.

