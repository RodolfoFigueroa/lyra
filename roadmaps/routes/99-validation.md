# 99 Final Validation

## Goal

Validate the complete route reshape and operations API before the TUI work
begins.

## Implementation Checklist

- [ ] `GET /data-types` replaced `GET /data_types`.
- [ ] `GET /lookups/met-zones` replaced `GET /met_zone_code`.
- [ ] `POST /admin/plugin-repos/{repo_id}/sync` replaced
      `POST /admin/plugin-repos/{repo_id}/pull`.
- [ ] `GET /jobs/{job_id}/result` returns stable JSON metadata/results.
- [ ] `GET /jobs/{job_id}/result/download` streams file bytes.
- [ ] Catalog refresh no longer restarts workers as an unconditional side
      effect.
- [ ] `POST /admin/workers/restart` performs explicit restart.
- [ ] `GET /admin/jobs` lists recent jobs without Redis key scans.
- [ ] `POST /admin/jobs/{job_id}/cancel` handles active, terminal, and missing
      jobs safely.
- [ ] `GET /health` reports liveness/readiness safely.
- [ ] `GET /admin/status` returns instance overview.
- [ ] `GET /admin/config-summary` returns no secrets.
- [ ] `GET /admin/catalog` returns catalog summary.
- [ ] `GET /admin/workers` handles online, offline, and unknown workers.
- [ ] `GET /admin/workers/{worker_name}` returns worker detail.
- [ ] `GET /admin/queues` summarizes queue assignment and consumer state.
- [ ] `packages/lyra_api` wraps all new routes for sync and async clients.
- [ ] End-to-end validation uses `tests/fixtures/plugins/smoke_plugin` through a
      `dir://` source instead of external plugin repositories.
- [ ] Docs and README mention only the final route names.

## Repository-Wide Commands

Run these after all implementation steps are complete:

```bash
uv sync
uv run ruff format
uv run ruff check
uv run ty check
uv run pytest
```

If docs or generated API references changed, also run the relevant docs command
from `docs/package.json`, usually:

```bash
npm run generate:api --prefix docs
```

If dependencies or lockfiles change, run the appropriate `uv` command and commit
the resulting lockfile changes:

```bash
uv lock
```

## Services And Cleanup

Route validation is not complete until every service, container, watcher, or
background process started for validation has been stopped, unless the user
explicitly asks to leave it running.

### Preferred Local Service Shape

Use the existing local development setup that is already configured for the
machine. The validation service set is:

- Redis
- Lyra API
- at least one Lyra worker consuming the `interactive` queue
- optional Postgres only for metropolitan-zone lookup validation

For direct local processes from the repository root:

```bash
docker run -d --name lyra-route-validation-redis -p 6379:6379 redis:alpine
uv run python -m lyra_app.worker_launcher interactive
uv run python -m lyra_app.main
```

Record the Redis container name, API process ID, worker process ID, ports, and
terminal/session handles before running E2E checks.

Readiness checks:

```bash
curl http://localhost:5219/health
curl http://localhost:5219/data-types
```

Register the committed smoke plugin fixture through a `dir://` source visible to
the running API and worker:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d "{\"source\":\"dir://$(pwd)/tests/fixtures/plugins/smoke_plugin\",\"id\":\"smoke\"}"
```

If using Docker Compose instead of direct processes, bind-mount
`tests/fixtures/plugins/smoke_plugin` into every Lyra API and worker container at
the same absolute path, such as `/plugins/smoke_plugin`, then register
`dir:///plugins/smoke_plugin`. Record container names such as `lyra-dev`,
`lyra-redis-dev`, and the worker containers.

Teardown for direct local validation:

```bash
docker rm -f lyra-route-validation-redis
```

Also stop the API and worker processes using the recorded process IDs or
terminal/session handles.

Teardown for Docker Compose validation:

```bash
docker compose -f docker/docker-compose-dev.yml down
```

Cleanup verification:

```bash
docker ps --filter name=lyra-route-validation-redis
docker ps --filter name=lyra-dev
```

Both commands should show no validation containers unless the user explicitly
requested that they stay running.

## End-To-End Scenarios

### Public Catalog And Lookup

1. Start a configured Lyra API.
2. Call `GET /health` and verify Redis readiness is represented.
3. Call `GET /data-types` and verify grouped `location` and `bounds` wrappers.
4. Register `tests/fixtures/plugins/smoke_plugin` through a `dir://` source and
   refresh the catalog.
5. Call `GET /metrics` and verify `smoke_table_metric`,
   `smoke_file_metric`, and `smoke_cancel_metric` are present.
6. Call `GET /metrics/smoke_table_metric`.
7. If a configured Postgres dataset is available, call
   `GET /lookups/met-zones?name=...` with a known name and verify the code
   response. Otherwise, validate this route through focused route tests.

### Job Result Flow

1. Submit a `smoke_table_metric` job through `POST /jobs` with a `geojson`
   location wrapper from `tests/smoke_plugin_helpers.py`.
2. Watch `GET /jobs/{job_id}/events` until terminal.
3. Fetch `GET /jobs/{job_id}/result` and verify JSON table result.
4. Submit a `smoke_file_metric` job with the same `geojson` location wrapper.
5. Fetch `GET /jobs/{job_id}/result` and verify JSON file metadata.
6. Fetch `GET /jobs/{job_id}/result/download` twice while the file exists and
   verify both calls return file bytes.

### Admin Plugin Operations

1. Use bearer auth for all `/admin` routes.
2. Add the smoke plugin directory source through `POST /admin/plugin-repos` with
   source `dir://.../tests/fixtures/plugins/smoke_plugin`.
3. Sync it through `POST /admin/plugin-repos/{repo_id}/sync` and verify
   directory edits would be reflected by sync in focused tests.
4. Refresh catalog through `POST /admin/plugin-catalog/refresh`.
5. Confirm workers are not restarted by refresh alone.
6. Restart workers through `POST /admin/workers/restart`.
7. Inspect routing through `GET /admin/plugin-routing`.

### Admin Job Operations

1. Submit several smoke plugin jobs.
2. Verify `GET /admin/jobs` returns them newest-first.
3. Filter by status and metric.
4. Cancel an active or queued `smoke_cancel_metric` job through
   `POST /admin/jobs/{job_id}/cancel`.
5. Verify terminal jobs are not overwritten by cancellation.
6. Verify unknown job cancellation returns `404`.

### Observability

1. Call `GET /admin/status`.
2. Call `GET /admin/config-summary` and verify no secret values are present.
3. Call `GET /admin/catalog`.
4. Call `GET /admin/workers` while workers are online.
5. Call `GET /admin/workers` when Celery inspect returns no data and verify the
   response is graceful.
6. Call `GET /admin/queues` and verify allowed queues, default queue, consumers,
   and assignment counts.

### Client Contract

1. Use `LyraAPIClient` for each public and admin route.
2. Use `AsyncLyraAPIClient` for the same operations.
3. Confirm bearer auth is sent on admin methods.
4. Confirm clients do not import `lyra_app`.

## Regression Checks

- OpenAPI should not contain:
  - `/data_types`
  - `/met_zone_code`
  - `/admin/plugin-repos/{repo_id}/pull`
- `POST /admin/plugin-catalog/refresh` should not call worker restart helpers.
- `GET /jobs/{job_id}/result` should not return file bytes.
- File download should not delete stored result metadata.
- `GET /admin/jobs` should not scan Redis keys in request handlers.
- E2E validation should not depend on external plugin repositories or generated
  local git repositories.
- `dir://` plugin sources should be handled as directory snapshots, not git
  repositories.
- Worker routes should not fail with 500 just because Celery inspect returns
  `None`.
- Config/status routes should not expose admin API keys, database passwords, or
  service account contents.
- The future TUI boundary should remain `packages/lyra_api`, not `lyra_app`.

## Pass Criteria

- All repository-wide commands pass.
- All route names in docs, tests, and clients match the final route surface.
- End-to-end scenarios pass against a local configured instance.
- Any remaining manual limitations are documented in the relevant docs page and
  do not block a TUI from using the HTTP API as its only backend.

## Fail Criteria

- Any old route remains as the only documented route.
- Any admin route exposes secrets.
- Job cancellation can overwrite a completed job result.
- File result inspection is destructive.
- Worker observability fails when workers are offline.
- The client package lacks wrappers for routes required by the TUI.
