# 01 Route Cleanup

## Goal

Normalize existing route names and remove awkward root-level helper routes before
the TUI targets the API.

## Background From The Discussion

Compatibility is not important yet. We agreed to prefer hyphenated paths and
clear resource namespaces. The current route surface includes:

- `GET /data_types`
- `GET /met_zone_code`
- `POST /admin/plugin-repos/{repo_id}/pull`

These should become:

- `GET /data-types`
- `GET /lookups/met-zones?name=...`
- `POST /admin/plugin-repos/{repo_id}/sync`

## Scope

- Rename the data type route path.
- Rename the metropolitan zone lookup path and module if helpful.
- Rename plugin repo `pull` route to `sync`.
- Update direct route tests, OpenAPI tests, docs, README examples, generated
  docs source references, and client paths where affected.

## Out Of Scope

- Do not add new job listing, cancellation, health, worker, or queue routes.
- Do not change route behavior beyond path names and wording.
- Do not implement TUI code.

## Files Or Areas Likely Affected

- `lyra_app/routes/data_types.py`
- `lyra_app/routes/met_zone.py`
- `lyra_app/routes/admin.py`
- `lyra_app/main.py`
- `packages/lyra_api/src/lyra/api/client/sync.py`
- `packages/lyra_api/src/lyra/api/client/async_.py`
- `tests/test_data_types_route.py`
- `tests/test_update_plugins.py`
- `tests/test_removed_legacy_routes.py`
- `tests/test_api_client_jobs.py`
- `docs/src/content/docs/`
- `docs/public/llms.txt`
- `README.md`

## Required Behavior

- `GET /data-types` returns the same `DataTypesResponse` currently returned by
  `GET /data_types`.
- `GET /lookups/met-zones?name=...` returns the same response shape currently
  returned by `GET /met_zone_code?name=...`.
- `POST /admin/plugin-repos/{repo_id}/sync` performs the same repo sync behavior
  currently performed by `pull_plugin_repo`.
- The old paths should not appear in OpenAPI unless the implementer explicitly
  documents a temporary alias decision.
- Error status codes and response payloads should remain semantically unchanged.

## Implementation Notes

- Consider renaming `lyra_app/routes/met_zone.py` to `lookups.py` if it improves
  clarity. If doing so, update `lyra_app/main.py`.
- Rename `PullPluginRepoResponse` to a sync-oriented name if that does not create
  excessive churn.
- Keep admin authentication unchanged for the plugin repo sync route.
- Update `LyraAPIClient.get_data_types()` and
  `AsyncLyraAPIClient.get_data_types()` to call `/data-types`.
- Add or update client methods for plugin repo sync only if admin client methods
  already exist by the time this step is implemented. Otherwise defer typed
  admin client coverage to `06-client-contract.md`.

## Tests And Verification

- Run focused route tests:

  ```bash
  uv run pytest tests/test_data_types_route.py tests/test_update_plugins.py tests/test_removed_legacy_routes.py
  ```

- Run focused client tests if client paths changed:

  ```bash
  uv run pytest tests/test_api_client_jobs.py
  ```

- Run formatting and linting on touched Python files:

  ```bash
  uv run ruff format <touched-files>
  uv run ruff check <touched-files>
  ```

## Completion Criteria

- OpenAPI contains `/data-types`, `/lookups/met-zones`, and
  `/admin/plugin-repos/{repo_id}/sync`.
- OpenAPI no longer contains `/data_types`, `/met_zone_code`, or
  `/admin/plugin-repos/{repo_id}/pull`.
- Docs and tests reference the new paths.
- Existing behavior is preserved under the new route names.

## Handoff Notes For The Next Step

The next step changes job result behavior. Do not start it until route-name
tests and docs are aligned, because client path updates will continue there.
