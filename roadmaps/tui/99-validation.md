# Final Validation

## Goal

Validate the completed Lyra TUI end to end after `01-package-scaffold.md` through
`05-docs-and-polish.md` have all been implemented.

This pass should prove that the TUI is a first-party operator console that runs
from the uv workspace, uses the public HTTP API through `lyra-api`, handles
offline and unauthorized states gracefully, keeps TUI dependencies out of the
server runtime, and performs mutating admin actions only after explicit
confirmation.

## Scope

- Audit every TUI roadmap step against the behavior it promised.
- Run repository-wide Python validation.
- Run docs validation when TUI docs were touched.
- Smoke-test the TUI entry points.
- Validate dependency and boundary constraints.
- Exercise the TUI against a running Lyra development stack.
- Clean up every service, container, watcher, and background process started for
  validation.

## Out Of Scope

- Do not implement new TUI features during this pass.
- Do not broaden the API surface beyond the routes listed in `00-overview.md`.
- Do not add a Docker image or Compose service for the TUI.
- Do not leave temporary plugin repos, routes, jobs, services, or containers
  behind unless the user explicitly asks.

## Required Behavior

- Each implemented roadmap step must be audited with an explicit validation
  status.
- Repository checks must use the uv-managed Python environment.
- Live end-to-end validation must prefer `docker/docker-compose-dev.yml` when
  Docker Compose is available.
- Any skipped or blocked validation item must include a concrete reason in the
  final report.
- Any service, container, watcher, TUI process, or background process started for
  validation must be stopped before completion unless the user explicitly asks
  to leave it running.
- Mutating end-to-end scenarios must capture starting state and restore it when
  validation changes plugin repos, routes, jobs, or worker state.

## Implementation Notes

- Treat this file as a final validation step, not a feature implementation step.
- Do not edit production code while validating unless a validation command makes
  a mechanical formatting or lint fix that is relevant to the completed TUI
  work.
- Prefer fake-client and Textual pilot tests for repeatable behavior checks, and
  use the live stack only for integration behavior that tests cannot prove.
- Keep secrets out of logs and final reports. Record where
  `LYRA_ADMIN_API_KEY` came from, not its value.
- If the live stack or credentials are unavailable, continue with all validation
  that can run locally and mark affected live scenarios as `blocked`.

## Validation Status Labels

Use these labels in the final report for every checklist item:

- `satisfied`: verified successfully.
- `not applicable`: legitimately unnecessary for this validation pass, with the
  reason recorded.
- `not run`: skipped even though it was runnable, with the reason recorded.
- `blocked`: could not be run because of a missing credential, service, network,
  environment capability, or other external blocker.

## Roadmap Step Audit Checklist

### `01-package-scaffold.md`

- [ ] `packages/lyra_tui` exists as a uv workspace package.
- [ ] `packages/lyra_tui/pyproject.toml` declares `lyra-tui` and its console
  script.
- [ ] `uv run lyra-tui --help` displays startup options.
- [ ] `uv run python -m lyra.tui --help` reaches the same entry path.
- [ ] The package depends on `lyra-api` and `textual`.
- [ ] The root `lyra-app` runtime dependencies do not include `textual`.
- [ ] Basic package import and startup tests pass.

### `02-client-and-state.md`

- [ ] Runtime configuration supports host, secure/no-secure, timeout, admin key,
  and refresh interval.
- [ ] Public health fetch works without admin auth.
- [ ] Admin fetches require an admin key and report missing or invalid auth
  clearly.
- [ ] API connection failures are displayed without crashing the TUI.
- [ ] Failed refreshes do not prevent later successful refreshes.
- [ ] Polling does not block UI interaction.
- [ ] Polling stops when the app exits.
- [ ] State and client tests use fake clients or responses rather than requiring
  a live Lyra instance.

### `03-readonly-views.md`

- [ ] Dashboard shows API, Redis, catalog, queue, worker, and job-store status.
- [ ] Dashboard remains useful with only public health data when admin auth is
  unavailable.
- [ ] Jobs view lists recent jobs and selected-job details, including empty
  state behavior.
- [ ] Workers view distinguishes configured/offline, observed/online, and
  unknown inspect states.
- [ ] Queues view displays `pending_depth_unknown` honestly instead of reporting
  a false `0`.
- [ ] Plugin/catalog summary views display plugin repos and catalog metadata.
- [ ] Manual refresh is available while polling is enabled.
- [ ] Long IDs, metric names, plugin sources, and API error messages do not break
  layouts.

### `04-actions.md`

- [ ] Job cancellation is available only for appropriate selected jobs.
- [ ] Job cancellation requires confirmation that includes the job ID and status.
- [ ] Job cancellation handles `404`, `409`, and Redis unavailable responses
  cleanly.
- [ ] Worker restart requires confirmation and supports a timeout value.
- [ ] Worker restart shows the returned restart message and refreshes observed
  state.
- [ ] Plugin repo add, enable/disable, delete, and sync workflows use admin APIs.
- [ ] Plugin delete requires confirmation with the repo ID.
- [ ] Catalog refresh reports updated plugin information, assigned queues,
  fingerprint, and restart recommendation.
- [ ] Catalog refresh does not restart workers unless a separate restart
  confirmation is accepted.
- [ ] Routing assignment uses allowed queues when possible.
- [ ] Route deletion reports whether a route was actually deleted.
- [ ] Successful actions refresh the relevant TUI state.
- [ ] Expected action failures appear as operator-facing errors without crashing
  the TUI.

### `05-docs-and-polish.md`

- [ ] TUI usage docs explain workspace sync and launch commands.
- [ ] Docs explain host, secure/no-secure, timeout, refresh interval, and admin
  key behavior.
- [ ] Docs state that the TUI connects to a running Lyra API and does not start
  Redis, workers, Docker, or the API itself.
- [ ] Docs mention that mutating admin actions require Bearer auth.
- [ ] CLI help agrees with docs on defaults and auth behavior.
- [ ] Docs include local or Compose-backed validation startup guidance.
- [ ] Docs include troubleshooting notes for API offline, missing admin key,
  Redis unavailable, worker inspect unavailable, and unknown queue pending depth.
- [ ] The TUI package remains excluded from the API Docker runtime dependency
  set.

## Repository-Wide Commands

Run from `/home/lain/Documents/lyra`.

Start by syncing dependencies if the environment is stale or `uv.lock` changed:

```bash
uv sync
```

Run the Python validation suite:

```bash
uv run pytest
uv run ruff format
uv run ruff check --fix
uv run ty check --fix
```

If `ruff format`, `ruff check --fix`, or `ty check --fix` changes files, inspect
the diff, keep only relevant fixes, and rerun the affected command until it
passes cleanly.

Verify the TUI entry points:

```bash
uv run lyra-tui --help
uv run python -m lyra.tui --help
```

If TUI docs were changed, build the docs:

```bash
npm run build --prefix docs
```

## Tests And Verification

Run the sections below as the validation procedure:

- `Repository-Wide Commands`
- `Boundary And Regression Commands`
- `Services And Cleanup`
- `End-To-End Scenarios`
- `Regression Checks`
- `Step Exit Checklist`

Record all command results, live observations, blocked items, and cleanup status
in the final report.

## Boundary And Regression Commands

Run these from `/home/lain/Documents/lyra`.

Confirm the TUI does not import server internals:

```bash
rg "from lyra_app|import lyra_app" packages/lyra_tui
```

Expected result: no matches.

Confirm the server runtime dependencies do not include Textual:

```bash
rg -i "textual" pyproject.toml Dockerfile lyra_app packages/lyra_api packages/lyra_sdk packages/lyra_utils
```

Expected result: no matches outside comments or non-runtime documentation. The
expected Textual dependency location is `packages/lyra_tui/pyproject.toml`.

Confirm the TUI does not shell out to local service managers or mutate local
runtime configuration:

```bash
rg "subprocess|docker|celery|redis-cli|lyra.toml|/lyra_data/config" packages/lyra_tui
```

Expected result: no behavior that shells out to Docker, Redis, Celery, or edits
Lyra config files. Document any benign matches.

Confirm mutation workflows still require confirmation by test coverage and, when
possible, manual TUI observation:

```bash
uv run pytest packages/lyra_tui
```

If package-local tests are not organized under `packages/lyra_tui`, run the
narrowest existing test path that covers TUI behavior and record the path used.

## Services And Cleanup

Use the existing Docker Compose development stack for live validation unless it
is unavailable, unsuitable for the scenario, or the user explicitly rejects it.

Start services from `/home/lain/Documents/lyra`:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

Record these handles before running scenarios:

- Terminal or session ID running Compose.
- Container names shown by `docker compose -f docker/docker-compose-dev.yml ps`.
- API port, expected to be `5219`.
- The value source for `LYRA_ADMIN_API_KEY`; do not print secret values.
- Any temporary plugin source paths or repo IDs added during validation.
- Any metric routes changed during validation.
- Any job IDs created, observed, or cancelled during validation.
- Any separate terminal or process ID running `lyra-tui`.

Readiness checks:

```bash
curl http://localhost:5219/health
curl http://localhost:5219/metrics
curl http://localhost:5219/admin/status \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

If `LYRA_ADMIN_API_KEY` is unset or invalid, mark authenticated live scenarios
as `blocked` rather than pretending they passed.

Launch the TUI against the stack:

```bash
LYRA_ADMIN_API_KEY="${LYRA_ADMIN_API_KEY}" \
uv run lyra-tui --host localhost:5219 --no-secure
```

Record the terminal or process ID for the TUI. Stop it before finishing
validation unless the user explicitly asks to leave it running.

If Compose is unavailable or unsuitable, record the reason before falling back to
manual startup. Manual startup must record all process IDs, terminal session IDs,
container IDs, ports, and cleanup commands. Prefer existing documented startup
commands over ad hoc alternatives.

Teardown for the Compose stack:

```bash
docker compose -f docker/docker-compose-dev.yml down
```

Cleanup verification:

```bash
docker compose -f docker/docker-compose-dev.yml ps
```

Validation is not complete until every service, container, watcher, TUI process,
or background process started for validation has been stopped or intentionally
left running at the user's explicit request.

## End-To-End Scenarios

1. Launch without admin key.
   - Expected: public health appears if the API is reachable.
   - Expected: admin-only views show a clear auth-required or locked state.

2. Launch with valid admin key.
   - Expected: dashboard loads status, config/catalog summary, workers, queues,
     plugin repos, routing, and recent jobs.
   - Expected: refresh does not block UI navigation or interaction.

3. API offline or unreachable.
   - Stop the API, stop the Compose stack, or point the TUI at an unused port.
   - Expected: connection failure state appears without a crash.
   - Restore or repoint to the API.
   - Expected: a later refresh recovers without restarting the TUI.

4. Worker inspect unavailable or workers offline.
   - Use the dev stack state or temporarily stop worker containers if feasible.
   - Expected: workers view shows `unknown`, `offline`, or the API-provided
     observed status without inventing task counts.

5. Queue depth unknown.
   - Open the queues view with the dev stack.
   - Expected: `pending_depth_unknown=True` appears as unknown pending depth, not
     as `0`.

6. Plugin catalog refresh.
   - Capture the starting plugin repo list, catalog fingerprint, and routing
     table.
   - Use an existing safe plugin repo or a temporary test plugin source.
   - Trigger catalog refresh from the TUI.
   - Expected: result shows updated plugin information, assigned metric queues,
     fingerprint, and restart recommendation.
   - Expected: workers are not restarted unless the separate restart
     confirmation is accepted.
   - Cleanup: remove temporary plugin repos and restore any changed plugin state.

7. Worker restart.
   - Confirm restart with a timeout appropriate for the dev stack.
   - Expected: TUI shows the returned restart message.
   - Expected: workers return online or show accurate observed status after
     polling.

8. Job list and cancellation.
   - Create a job through an existing test metric or plugin if one is available
     in the validation stack.
   - Expected: job appears in recent jobs and selected-job details.
   - If the job is active, cancel it from the TUI and accept the confirmation.
   - Expected: cancellation result appears and later status reflects
     `cancelled`, or a clear `409` appears if it already reached a terminal
     state.

9. Routing workflow.
   - Capture the starting route for a non-critical metric.
   - Assign that metric to an allowed queue from the TUI.
   - Expected: routing table updates after refresh.
   - Delete the explicit route from the TUI.
   - Expected: route disappears or reports `deleted=false` accurately.
   - Cleanup: restore the starting route if validation changed it.

10. Graceful exit and cleanup.
    - Exit the TUI through its normal quit binding.
    - Stop every service or process started for validation.
    - Expected: no validation-started Compose stack, container, watcher, or TUI
      process remains running unless the user explicitly requested it.

## Regression Checks

- Confirm no TUI code imports `lyra_app`.
- Confirm no Dockerfile or server runtime dependency set installs `textual`.
- Confirm no TUI action shells out to Docker, Redis, Celery, or local config
  files.
- Confirm mutating actions require confirmation and are unavailable without a
  valid selection.
- Confirm missing admin auth does not prevent public health display.
- Confirm expected API errors are shown in the UI without crashing it.
- Confirm unknown queue pending depth is displayed honestly.
- Confirm CLI defaults match the docs.
- Confirm docs do not imply the TUI starts or manages the Lyra service stack.

## Step Exit Checklist

- [ ] Every item in `Roadmap Step Audit Checklist` is marked with a validation
  status.
- [ ] `uv sync` was run or explicitly marked not needed because dependencies
  were already synced.
- [ ] `uv run pytest` passed, or failures were investigated and reported.
- [ ] `uv run ruff format` completed; any resulting changes were inspected.
- [ ] `uv run ruff check --fix` passed, or failures were investigated and
  reported.
- [ ] `uv run ty check --fix` passed, or failures were investigated and
  reported.
- [ ] `uv run lyra-tui --help` passed.
- [ ] `uv run python -m lyra.tui --help` passed.
- [ ] `npm run build --prefix docs` passed when docs were changed, or was marked
  not applicable with the reason.
- [ ] Boundary and regression commands were run and results recorded.
- [ ] Live service readiness checks were run when the dev stack was available.
- [ ] End-to-end scenarios were run against a live stack, or each blocked/not-run
  scenario has a concrete reason.
- [ ] Temporary plugin repos, routes, jobs, and other validation data were
  cleaned up or intentionally retained with user approval.
- [ ] Every service, container, watcher, TUI process, or background process
  started for validation was stopped or intentionally left running at the user's
  explicit request.
- [ ] Final report includes validation statuses, remaining risks, and cleanup
  confirmation.

## Completion Criteria

Pass when:

- All required repository commands pass.
- TUI entry points run.
- Roadmap step audit items are satisfied or explicitly marked not applicable.
- Boundary checks prove the TUI uses the HTTP/API package boundary and keeps
  Textual out of the server runtime.
- End-to-end scenarios pass against a live Lyra stack, or any blocked scenario
  is due only to an external constraint that is clearly reported.
- Every validation-started service or process is stopped or intentionally left
  running at the user's explicit request.

Fail when:

- The TUI imports `lyra_app` internals.
- Textual becomes a server runtime dependency.
- Mutating actions can run without confirmation.
- API failures crash the TUI.
- The TUI shells out to Docker, Redis, Celery, or local config files for core
  behavior.
- Unknown queue depth is displayed as a known zero value.
- Validation-started services or processes are left running unintentionally.

## Final Report Requirements

End the validation pass with a supervised handoff that includes:

- Target roadmap file validated: `roadmaps/tui/99-validation.md`.
- Main files changed during validation, if any.
- Repository commands run and results.
- Boundary and regression command results.
- End-to-end scenarios run, with each marked `satisfied`, `not applicable`,
  `not run`, or `blocked`.
- Step exit checklist audit using the same status labels.
- Service and process cleanup confirmation.
- Confirmation that no later roadmap step was implemented.
- Confirmation that no commit was made unless the user explicitly requested one.
