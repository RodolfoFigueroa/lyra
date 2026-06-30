# 05 Observability Routes

## Goal

Add health, status, catalog, config, worker, and queue routes that give the TUI
a reliable operational overview.

## Background From The Discussion

The TUI needs to answer common operator questions:

- Is the API alive?
- Is Redis reachable?
- What catalog is loaded?
- What queues exist?
- Are workers online?
- Which workers consume which queues?
- Why might a job be stuck?

These should be answered through HTTP, not by making the TUI inspect Redis or
Celery directly.

## Scope

- Add `GET /health`.
- Add `GET /admin/status`.
- Add `GET /admin/config-summary`.
- Add `GET /admin/catalog`.
- Add `GET /admin/workers`.
- Add `GET /admin/workers/{worker_name}`.
- Add `GET /admin/queues`.
- Add SDK response models where useful.
- Update tests and docs.

## Out Of Scope

- Do not add WebSocket monitoring.
- Do not add persistent metrics storage.
- Do not expose secrets, service account paths beyond safe summaries, or raw
  environment variables.
- Do not require workers to be online for the API to start.

## Files Or Areas Likely Affected

- `lyra_app/routes/health.py` or similar new public route module
- `lyra_app/routes/admin.py` or new admin route modules
- `lyra_app/main.py`
- `lyra_app/config.py`
- `lyra_app/registry.py`
- `lyra_app/plugin_state.py`
- `lyra_app/worker_control.py`
- `lyra_app/celery_app.py`
- `lyra_app/db/redis.py`
- `packages/lyra_sdk/src/lyra/sdk/models/`
- `packages/lyra_api/src/lyra/api/client/sync.py`
- `packages/lyra_api/src/lyra/api/client/async_.py`
- `tests/test_runtime_config_usage.py`
- `tests/test_config_contract.py`
- `tests/test_registry_catalog.py`
- `tests/test_worker_control.py`
- `tests/test_update_plugins.py` or new admin observability tests
- `docs/src/content/docs/reference.md`
- `docs/src/content/docs/operations.md`
- `docs/src/content/docs/deployment.md`
- `docs/public/llms.txt`

## Required Behavior

### `GET /health`

- Does not require admin auth.
- Returns API liveness and Redis readiness.
- Should be safe for load balancers and local scripts.
- Should not initialize expensive external dependencies beyond what the app
  already initializes.

### `GET /admin/status`

- Requires admin auth.
- Returns a compact instance overview:
  - API version
  - Redis status
  - metric count
  - configured/default queues
  - worker count from config
  - job TTL
  - catalog fingerprint if available

### `GET /admin/config-summary`

- Requires admin auth.
- Returns secret-free runtime settings:
  - API host/port
  - allowed queues
  - default queue
  - worker names, queues, concurrency
  - job-store TTL
  - plugin catalog/state/cache paths only if considered safe
- Must not return admin API key, Postgres password, service account contents, or
  raw environment variables.

### `GET /admin/catalog`

- Requires admin auth.
- Returns loaded catalog metadata:
  - metric count
  - metric names
  - catalog fingerprint if available
  - plugin source summary if available, including source kind such as `github`,
    `local`, or `directory`
  - route assignments if useful

### `GET /admin/workers`

- Requires admin auth.
- Uses Celery inspect defensively.
- Returns configured workers and observed workers where possible.
- Includes online/offline or unknown state.
- Includes active/reserved/scheduled task counts when available.

### `GET /admin/workers/{worker_name}`

- Requires admin auth.
- Returns details for one configured or observed worker.
- Includes queues, active tasks, reserved tasks, scheduled tasks, and stats when
  available.
- Returns `404` for unknown names only if the name is neither configured nor
  observed.

### `GET /admin/queues`

- Requires admin auth.
- Returns allowed queues, default queue, metric assignment counts, configured
  worker consumers, observed worker consumers, and pending depth if safely
  available.
- If queue depth cannot be determined reliably, return `null` or an explicit
  `unknown` field instead of guessing.

## Implementation Notes

- Celery `inspect()` often returns `None`; treat that as unknown/offline, not as
  a server error.
- Prefer small helper functions in `worker_control.py` for inspect calls so route
  tests can monkeypatch them.
- Avoid exposing raw Celery task payloads if they could contain user data. Return
  task IDs, names, worker names, and timestamps where safe.
- Use Pydantic response models for route stability.
- If registry fingerprint is not exposed, add a small read-only helper in
  `lyra_app/registry.py` rather than reaching into private globals from routes.
- When exposing plugin source summaries, do not assume every source is a git
  repository. Preserve source kind so the TUI can distinguish GitHub,
  `file://`, and `dir://` sources.

## Tests And Verification

- Add tests for:
  - healthy Redis
  - Redis unavailable
  - status response excludes secrets
  - config summary excludes secrets
  - catalog metadata with empty catalog
  - catalog metadata populated from `tests/fixtures/plugins/smoke_plugin` through
    a `dir://` source
  - workers route with inspect data
  - workers route with inspect returning `None`
  - queues route with configured queues and metric assignments
- Run:

  ```bash
  uv run pytest tests/test_worker_control.py tests/test_runtime_config_usage.py tests/test_config_contract.py
  uv run pytest tests/test_registry_catalog.py tests/test_update_plugins.py
  uv run ruff format <touched-files>
  uv run ruff check <touched-files>
  ```

## Completion Criteria

- The TUI can render a useful home/status screen with no direct backend access.
- Worker and queue routes are resilient when workers are offline.
- No route exposes secrets.
- Docs describe which observability values may be unknown.

## Handoff Notes For The Next Step

The next step updates `packages/lyra_api` as the client contract for all route
work. Make sure all response shapes are stable enough to wrap.
