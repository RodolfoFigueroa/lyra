# 03 Catalog Refresh And Worker Restart

## Goal

Separate catalog refresh from worker restart so the TUI can present them as
distinct operator actions.

## Background From The Discussion

`POST /admin/plugin-catalog/refresh` currently syncs enabled plugin sources,
refreshes the API catalog, assigns missing routes, and restarts workers. Sources
may be GitHub entries, `file://` local git repositories, or `dir://` directory
snapshots. The combined operation is convenient for scripts but too much hidden
behavior for an operator console.

## Scope

- Change `POST /admin/plugin-catalog/refresh` so it refreshes catalog state but
  does not restart workers.
- Add `POST /admin/workers/restart` for explicit worker restarts.
- Preserve the existing graceful restart timeout behavior on the new route.
- Update tests and docs to describe the two-step operation.

## Out Of Scope

- Do not add detailed worker status or queue observability yet.
- Do not implement job cancellation.
- Do not change the worker launcher.

## Files Or Areas Likely Affected

- `lyra_app/routes/admin.py`
- `lyra_app/worker_control.py`
- `tests/test_update_plugins.py`
- `tests/test_worker_control.py`
- `docs/src/content/docs/getting-started.md`
- `docs/src/content/docs/job-api.md`
- `docs/src/content/docs/deployment.md`
- `docs/src/content/docs/plugin-quickstart.md`
- `docs/src/content/docs/plugin-author-checklist.md`
- `docs/src/content/docs/reference.md`
- `docs/public/llms.txt`

## Required Behavior

- `POST /admin/plugin-catalog/refresh`:
  - Requires admin bearer auth.
  - Syncs enabled plugin sources as it does today, including `dir://` directory
    snapshots.
  - Refreshes the API catalog.
  - Auto-assigns missing metric routes as it does today.
  - Does not call `graceful_worker_restart()`.
  - Returns a response that makes clear whether workers need restart, if that can
    be known cheaply.
- `POST /admin/workers/restart`:
  - Requires admin bearer auth.
  - Accepts a timeout query parameter or strict request body field.
  - Calls `graceful_worker_restart(timeout=...)`.
  - Returns an explicit response instead of only relying on logs.

## Implementation Notes

- Reuse the existing timeout validation from `_TIMEOUT_QUERY` if the route keeps
  the query parameter style.
- Consider a response model such as:

  ```python
  class WorkerRestartResponse(BaseModel):
      requested: bool
      timeout: float
  ```

- Keep more detailed restart result reporting for a later worker observability
  step unless it is easy to expose from `worker_control.py`.
- Update docs that currently say catalog refresh restarts workers.

## Tests And Verification

- Update tests that currently expect `refresh_plugin_catalog()` to restart
  workers.
- Keep or add coverage that refresh handles the smoke plugin directory source
  from `tests/fixtures/plugins/smoke_plugin`.
- Add tests that `restart_workers()` calls `graceful_worker_restart()` with the
  requested timeout.
- Confirm OpenAPI includes `/admin/workers/restart`.
- Run:

  ```bash
  uv run pytest tests/test_update_plugins.py tests/test_worker_control.py
  uv run ruff format <touched-files>
  uv run ruff check <touched-files>
  ```

## Completion Criteria

- Catalog refresh and worker restart are separate admin routes.
- Refresh tests prove workers are not restarted as a side effect.
- Restart tests prove the timeout is passed through.
- Docs show the operator flow as refresh catalog, then restart workers when
  needed.

## Handoff Notes For The Next Step

The next step adds job listing and cancellation. It may reuse worker control for
Celery task revocation, but it should not re-couple catalog refresh and restart.
