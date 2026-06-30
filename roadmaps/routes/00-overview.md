# Route Reshape And Operations API Overview

## Goal

Reshape Lyra's HTTP API into the route surface needed by a future Textual TUI
for managing a running Lyra instance.

The TUI should talk only to HTTP routes through `packages/lyra_api`. It should
not import `lyra_app`, read Redis directly, or call Celery directly.

## Agreed Decisions

- Keep this work in the existing monorepo.
- The eventual TUI should live in a separate workspace package, likely
  `packages/lyra_tui`, but this roadmap is only for the API route work.
- Backward compatibility is not required because the app has not been published.
- Use hyphenated route paths for public API names.
- Keep public catalog/execution routes separate from authenticated admin and
  operator routes.
- Add missing routes before building the TUI so the TUI does not infer state from
  implementation details.
- Keep admin authentication on instance-wide control routes.
- Avoid hidden side effects in routes. In particular:
  - Catalog refresh should not always restart workers.
  - Fetching generic job result metadata should not delete file artifacts.
- End-to-end validation should use the committed smoke plugin fixture at
  `tests/fixtures/plugins/smoke_plugin` through a `dir://` directory source.
  Do not depend on external plugin repositories or generated local git repos for
  route validation.

## Desired Route Surface

### Public Routes

- `GET /health`
- `GET /data-types`
- `GET /metrics`
- `GET /metrics/{metric_name}`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/events`
- `GET /jobs/{job_id}/result`
- `GET /jobs/{job_id}/result/download`
- `GET /lookups/met-zones?name=...`

### Admin Job Routes

- `GET /admin/jobs`
- `POST /admin/jobs/{job_id}/cancel`

### Admin Instance Routes

- `GET /admin/status`
- `GET /admin/config-summary`
- `GET /admin/catalog`

### Admin Worker And Queue Routes

- `GET /admin/workers`
- `GET /admin/workers/{worker_name}`
- `POST /admin/workers/restart`
- `GET /admin/queues`

### Admin Plugin Routes

- `GET /admin/plugin-repos`
- `POST /admin/plugin-repos`
- `PATCH /admin/plugin-repos/{repo_id}`
- `DELETE /admin/plugin-repos/{repo_id}`
- `POST /admin/plugin-repos/{repo_id}/sync`
- `POST /admin/plugin-catalog/refresh`
- `GET /admin/plugin-routing`
- `PUT /admin/plugin-routing/{metric_name}`
- `DELETE /admin/plugin-routing/{metric_name}`

## Routes To Remove Or Replace

- Replace `GET /data_types` with `GET /data-types`.
- Replace `GET /met_zone_code` with `GET /lookups/met-zones`.
- Replace `POST /admin/plugin-repos/{repo_id}/pull` with
  `POST /admin/plugin-repos/{repo_id}/sync`.
- Stop using `GET /jobs/{job_id}/result` as an overloaded endpoint that can
  consume/delete file results. Add `/download` for file bytes.
- Stop restarting workers as an unconditional side effect of
  `POST /admin/plugin-catalog/refresh`.

## Non-Goals

- Do not implement the Textual TUI in this route roadmap.
- Do not add direct Redis or Celery access to `packages/lyra_api`.
- Do not preserve old paths as aliases unless a later implementer deliberately
  decides to keep temporary aliases for local migration.
- Do not expose secrets in any config or status response.
- Do not make the public API key-protected as part of this roadmap, except where
  routes are explicitly under `/admin`.
- Do not require external plugin repositories for validation.

## Assumptions

- `lyra_app/routes/admin.py` remains the home for admin-authenticated routes
  unless a step explicitly splits it into focused route modules.
- New public route modules may be added under `lyra_app/routes/`.
- Public response models that are useful to clients should live in
  `packages/lyra_sdk/src/lyra/sdk/models/`.
- Client methods for all new public and admin routes should be added to
  `packages/lyra_api/src/lyra/api/client/sync.py` and
  `packages/lyra_api/src/lyra/api/client/async_.py`.
- The final TUI will use `packages/lyra_api`, so each route added here should
  have a typed client wrapper unless it is intentionally internal.
- Plugin operations should treat GitHub entries, `file://` local git
  repositories, and `dir://` directory snapshots as plugin sources. The existing
  `/admin/plugin-repos` route name remains the public path even when the source
  is a directory.
- The committed smoke plugin fixture exposes `smoke_table_metric`,
  `smoke_file_metric`, and `smoke_cancel_metric` for end-to-end route
  validation.

## Known Risks

- `GET /admin/jobs` likely needs new Redis indexing in `lyra_app/job_store.py`.
  Existing storage supports known-job lookups but not efficient listing.
- Job cancellation must coordinate job-store state and Celery task revocation
  without corrupting terminal results.
- Celery `inspect()` calls can return `None` when workers are offline or
  unreachable. Worker/queue routes must degrade gracefully.
- The current file-result route schedules cleanup after file download. Changing
  this may require explicit result/artifact cleanup behavior later.
- `GET /admin/catalog` may need small registry metadata additions if the current
  registry does not expose catalog fingerprint or plugin-source details cleanly.
- Container-based validation must bind-mount any `dir://` smoke plugin source
  into every API and worker container at the same absolute path used in the
  admin API request.

## Execution Order

1. Route naming cleanup and lookup reshaping.
2. Job result metadata/download split.
3. Catalog refresh and worker restart separation.
4. Job listing and cancellation.
5. Health, status, catalog, config, worker, and queue observability.
6. `lyra-api` client contract updates for all new routes.
7. Final end-to-end validation.

Each numbered plan file assumes earlier numbered files are complete.
