# Final Validation

## Goal

Validate the completed Lyra TUI end to end against both fake/test clients and a running Lyra development stack.

## Implementation Checklist

- `packages/lyra_tui` exists as a uv workspace package.
- `lyra-tui` console script exists.
- `python -m lyra.tui` works.
- Textual is a dependency of `lyra-tui`, not a runtime dependency of `lyra-app`.
- TUI configuration supports host, secure/no-secure, timeout, admin key, and refresh interval.
- Public health works without admin auth.
- Admin views show a clear locked/error state when admin auth is missing or invalid.
- Dashboard shows API, Redis, catalog, queue, worker, and job-store status.
- Jobs view lists recent jobs and selected-job details.
- Workers and queues views show configured/observed state and unknown queue depth truthfully.
- Plugin and catalog views show plugin repos, catalog summary, and refresh results.
- Routing view shows metric queue assignments and allowed/default queues.
- Job cancellation requires confirmation.
- Worker restart requires confirmation and timeout input.
- Plugin add/update/delete/sync actions use admin APIs and refresh state.
- Catalog refresh reports restart recommendation and does not restart workers without explicit confirmation.
- Routing changes use admin APIs and refresh state.
- Expected API failures do not crash the TUI.
- Docs explain how to run and authenticate the TUI.

## Repository-Wide Commands

Run from `/home/lain/Documents/lyra`:

```bash
uv sync
uv run pytest
uv run ruff format
uv run ruff check --fix
uv run ty check --fix
```

If docs were changed:

```bash
npm run build --prefix docs
```

Also verify entry points:

```bash
uv run lyra-tui --help
uv run python -m lyra.tui --help
```

## Services And Cleanup

Prefer the existing Docker Compose development stack for end-to-end validation.

Start services from `/home/lain/Documents/lyra`:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

Record:

- Terminal/session ID running Compose.
- Container names:
  - `lyra-dev`
  - `lyra-redis-dev`
  - `lyra-celery-worker-interactive-dev`
  - `lyra-celery-worker-batch-dev`
- API port: `5219`.
- Any test plugin source paths added during validation.
- Any job IDs created or cancelled during validation.

Readiness checks:

```bash
curl http://localhost:5219/health
curl http://localhost:5219/metrics
curl http://localhost:5219/admin/status \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

Run the TUI against the stack:

```bash
LYRA_ADMIN_API_KEY="${LYRA_ADMIN_API_KEY}" \
uv run lyra-tui --host localhost:5219 --no-secure
```

If Compose is unavailable or unsuitable, fall back to manual startup only after recording why:

```bash
docker run -d -p 6379:6379 redis:alpine
uv run python -m lyra_app.worker_launcher interactive
uv run python -m lyra_app.main
```

For manual startup, record all process IDs, terminal session IDs, container IDs, and ports.

Teardown for Compose:

```bash
docker compose -f docker/docker-compose-dev.yml down
```

Cleanup verification:

```bash
docker compose -f docker/docker-compose-dev.yml ps
```

Validation is not complete until every service, container, watcher, or background process started for validation has been stopped or intentionally left running at the user's explicit request.

## End-To-End Scenarios

1. Launch without admin key.
   - Expected: public health appears if API is reachable.
   - Expected: admin-only views show a clear auth-required state.

2. Launch with valid admin key.
   - Expected: dashboard loads status, config/catalog summary, workers, queues, plugin repos, and recent jobs.
   - Expected: refresh does not block UI interaction.

3. API offline.
   - Stop or point away from the API.
   - Expected: connection failure state appears.
   - Restart or repoint to API.
   - Expected: a later refresh recovers without restarting the TUI.

4. Worker inspect unavailable or workers offline.
   - Expected: workers view shows `unknown` or `offline` state from API responses without inventing task counts.

5. Queue depth unknown.
   - Expected: queues view displays unknown pending depth instead of `0`.

6. Plugin catalog refresh.
   - Use an existing safe plugin repo/source if available.
   - Expected: refresh result shows updated plugins, assigned metric queues, fingerprint, and restart recommendation.
   - Expected: workers are not restarted unless the separate restart confirmation is accepted.

7. Worker restart.
   - Confirm restart with a small timeout appropriate for the dev stack.
   - Expected: TUI shows returned restart message.
   - Expected: workers come back online or show accurate observed status after polling.

8. Job list and cancellation.
   - Create a job through an existing test metric/plugin if one is available in the validation stack.
   - Expected: job appears in recent jobs.
   - If the job is active, cancel it from the TUI and confirm.
   - Expected: cancellation result appears and later status reflects `cancelled`, or a clear `409` appears if it already reached a terminal state.

9. Routing workflow.
   - Pick a non-critical metric and assign it to an allowed queue.
   - Expected: routing table updates.
   - Delete the explicit route.
   - Expected: route disappears or reports `deleted=false` accurately.

## Regression Checks

- Confirm no TUI code imports `lyra_app`.
- Confirm no Dockerfile change installs Textual into the server runtime image unless explicitly justified.
- Confirm no TUI action shells out to Docker, Redis, Celery, or filesystem config files.
- Confirm mutating actions require confirmation.
- Confirm missing admin auth does not prevent public health display.
- Confirm queue pending depth unknown is displayed honestly.
- Confirm CLI defaults match docs.

## Pass/Fail Criteria

Pass when:

- All repository commands pass.
- TUI entry points run.
- Headless tests cover core state, rendering, and action behavior.
- End-to-end scenarios pass against a live Lyra stack.
- Every validation service started during the pass is stopped or explicitly left running by the user.

Fail when:

- The TUI requires importing server internals.
- Textual becomes a server runtime dependency.
- Mutating actions can run without confirmation.
- API failures crash the TUI.
- Validation services are left running unintentionally.

