# Runtime Data Flow

## Config Loading

`lyra.toml` loads deployment config only.

`LyraConfig.plugins` keeps:

- `catalog_dir`,
- `runner_base_dir`,
- `default_queue`,
- `allowed_queues`.

It no longer contains:

- `repos`,
- `metric_queues`.

The config loader should reject those removed fields as unknown inputs.

## State Loading

Runtime code reads `/lyra_data/state/plugins.toml` through a dedicated state
service.

The state service owns:

- parsing,
- validation,
- normalization,
- atomic writes,
- default empty state creation,
- helper methods for repo CRUD and routing CRUD.

Registry code, worker code, and admin routes should use this state service
instead of reading or writing TOML directly.

## API Catalog Refresh

Catalog refresh must:

1. load `LyraConfig`,
2. load plugin state,
3. sync enabled repos into `config.plugins.catalog_dir`,
4. parse manifests,
5. collect metric names,
6. create missing `metric_queues` entries in state using
   `config.plugins.default_queue`,
7. reload updated state if it changed,
8. build the API registry from manifests plus state routing,
9. update the catalog fingerprint.

Disabled repos are ignored.

If a cataloged metric does not have a valid assignment after the auto-assignment
step, refresh fails.

## API Dispatch

Job submission must dispatch using queue assignments from plugin state.

Dispatch must not read queue assignments from `lyra.toml`.

## Worker Startup

Worker startup must:

1. load `LyraConfig`,
2. load plugin state,
3. read the selected `[workers.<name>]` table from `lyra.toml`,
4. sync enabled repos into the worker install directory,
5. install compatible repos,
6. read manifests,
7. import only metrics whose state-owned queue appears in the worker queues.

Workers never mutate `plugins.toml`.

## Failure Behavior

Invalid deployment config is fatal.

Invalid plugin state is fatal for startup, catalog refresh, and worker registry
refresh.

Repo source validation should happen at API write time. Runtime sync should not
silently skip malformed state entries.

Git sync failures during catalog refresh should be reported in admin API
responses. Existing local checkouts may still be used for a repo if the
implementation deliberately preserves the current fallback behavior, but the
response must make the failure visible.

