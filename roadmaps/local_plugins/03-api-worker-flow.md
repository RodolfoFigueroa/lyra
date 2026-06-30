# API and Worker Flow

## Goal

Make directory sources behave correctly through the admin API, catalog refresh,
and worker plugin installation flows.

## Background from the discussion

The API and workers already share `sync_plugin_repos()`. That is the right
integration point. Directory sources should keep this architecture intact:
state entries are synced into Lyra-owned directories, the API reads manifests,
and workers install copied snapshots.

## Scope

- Update admin error handling if directory sync introduces non-git errors.
- Add or update route, registry, and worker tests.
- Confirm existing refresh and worker restart semantics remain unchanged.

## Out of scope

- Do not add hot reload.
- Do not import plugin code while loading the API catalog.
- Do not change metric routing behavior.
- Do not change worker queue assignment logic.

## Files or areas likely affected

- `lyra_app/routes/admin.py`
- `lyra_app/registry.py`
- `lyra_app/worker.py`
- `tests/test_update_plugins.py`
- `tests/test_registry_catalog.py`
- `tests/test_runner.py`
- Possibly `tests/test_metrics_route.py` or `tests/test_jobs_route.py` if shared
  fixtures need source-kind updates.

## Required behavior

- `POST /admin/plugin-repos` accepts a valid `dir://` source and stores the
  normalized source.
- `GET /admin/plugin-repos` returns the normalized `dir://` source with
  `ref=None`.
- `POST /admin/plugin-repos/{repo_id}/pull` syncs a directory source into the
  catalog directory and returns the existing pull response shape.
- Pulling or refreshing a missing directory source returns a clear server-side
  sync error rather than an unhandled traceback.
- `refresh_catalog()` includes directory sources when building the metric
  registry.
- `refresh_catalog()` still assigns missing metric queues based on manifest
  metric names.
- `load_runner_metric_entries()` installs copied directory snapshots using the
  existing `install_runner_plugins()` path.
- The API catalog still does not import plugin modules.
- Existing GitHub and local git admin behavior remains unchanged.

## Implementation notes

- If `PluginSyncError` was added in step 2, catch it alongside
  `subprocess.CalledProcessError` where admin routes convert sync failures into
  HTTP errors.
- Consider renaming `_git_error_detail()` in `lyra_app/routes/admin.py` to a
  generic `_sync_error_detail()` while preserving existing git error output.
- `lyra_app/registry.py` and `lyra_app/worker.py` may not need code changes if
  `sync_plugin_repos()` returns correct `SyncedPluginRepo` objects. Add tests to
  lock that in.
- For worker tests, continue monkeypatching install behavior where appropriate
  to avoid exercising package installation unless a focused integration test is
  worth the cost.

## Tests and verification

- Extend admin endpoint tests to create, list, and pull a `dir://` source.
- Extend admin failure tests for missing directory sync errors.
- Extend registry tests so enabled state repos can include a directory source.
- Add a registry test that a manifest copied from a directory source is loaded
  without importing plugin code.
- Extend worker tests to confirm runner sync receives `dir://` entries and
  installs from the copied target path.
- Keep existing tests that assert GitHub refs and local git behavior.

Run:

```bash
uv run pytest tests/test_update_plugins.py tests/test_registry_catalog.py tests/test_runner.py
```

## Completion criteria

- Directory sources work through admin pull and catalog refresh.
- Worker loading uses the directory snapshot path, not the original source path.
- Sync errors are user-visible and test-covered.
- Existing route, registry, and worker behavior for git sources is unchanged.

## Handoff notes for the next step

The next step should update user-facing documentation and Docker examples to
make the development-only contract clear.

