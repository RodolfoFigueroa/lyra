# Lyra TUI Implementation Overview

## Feature

Build a first-party terminal UI for managing a running Lyra instance.

The TUI should be an operator console for local development and deployment support. It should connect to a running Lyra API over HTTP, use the public `lyra-api` client package, and avoid importing `lyra_app` internals.

## Agreed Decisions

- Implement the TUI as a workspace package under `packages/lyra_tui`.
- Use Textual as the TUI framework.
- Expose a console script named `lyra-tui`.
- Use `lyra-api` and SDK response models as the integration boundary.
- Keep Textual and other TUI-only dependencies out of the `lyra-app` server runtime and Docker image.
- Do not create a separate repository for the first version.
- Do not do a broad API refactor before building the TUI. The existing admin routes are good enough for an MVP.

## Current API Surface To Use

- Public health route: `GET /health`.
- Admin status/config/catalog routes:
  - `GET /admin/status`
  - `GET /admin/config-summary`
  - `GET /admin/catalog`
- Worker and queue routes:
  - `GET /admin/workers`
  - `GET /admin/workers/{worker_name}`
  - `GET /admin/queues`
  - `POST /admin/workers/restart`
- Job routes:
  - `GET /admin/jobs`
  - `POST /admin/jobs/{job_id}/cancel`
  - `GET /jobs/{job_id}`
  - `GET /jobs/{job_id}/events`
  - `GET /jobs/{job_id}/result`
- Plugin and routing routes:
  - `GET /admin/plugin-repos`
  - `POST /admin/plugin-repos`
  - `PATCH /admin/plugin-repos/{repo_id}`
  - `DELETE /admin/plugin-repos/{repo_id}`
  - `POST /admin/plugin-repos/{repo_id}/sync`
  - `POST /admin/plugin-catalog/refresh`
  - `GET /admin/plugin-routing`
  - `PUT /admin/plugin-routing/{metric_name}`
  - `DELETE /admin/plugin-routing/{metric_name}`

## Non-Goals For MVP

- Do not embed or manage the API, Redis, workers, or Docker Compose stack from inside the TUI.
- Do not import server internals such as `lyra_app.job_store`, `lyra_app.worker_control`, or `lyra_app.config`.
- Do not build a web UI.
- Do not add shelling-out behavior for `docker`, `uv`, or `celery`.
- Do not make the TUI responsible for editing `/lyra_data/config/lyra.toml`.
- Do not solve every observability gap before the TUI exists.

## Rejected Approaches

- Separate repo: rejected for now because the TUI should evolve with Lyra's admin API and SDK models.
- Package inside `lyra_app`: rejected because it would couple an operator UI to server runtime internals and dependencies.
- Curses or low-level terminal UI: rejected because Textual provides richer widgets, async workflows, and test support.
- Direct Redis/Celery access from the TUI: rejected because the HTTP admin API should remain the management boundary.

## Assumptions

- Operators will pass the API host and admin key through command-line flags or environment variables.
- The default local target is `localhost:5219` with `secure=False`.
- The TUI should run from the workspace with `uv run lyra-tui` after dependencies are synced.
- The admin key can be read from `LYRA_ADMIN_API_KEY` when not supplied explicitly.
- Heavy polling should be modest by default and configurable later if needed.
- Queue pending depth is useful but not required for the first usable version. `/admin/queues` currently reports `pending_depth_unknown=True`.

## Risks

- Textual introduces a new dependency family; keep it isolated to `packages/lyra_tui`.
- The current async client opens a session per request. This is acceptable for an MVP, but heavy polling may motivate a shared-session client later.
- Mutating actions need confirmations so a stray keypress does not cancel jobs or restart workers.
- The TUI will need graceful degraded states for missing admin auth, offline API, unavailable Redis, and unavailable Celery inspect.
- Terminal UI tests can become brittle if they assert exact layout instead of behavior and key state.

## Execution Order

1. Create and wire the `lyra-tui` workspace package.
2. Build the shared TUI app shell, configuration, client adapter, polling model, and error handling.
3. Implement the read-mostly dashboard, jobs, workers, and queues views.
4. Implement mutating plugin, routing, job cancellation, and worker restart workflows with confirmations.
5. Add docs and integration polish.
6. Run the final validation pass.

