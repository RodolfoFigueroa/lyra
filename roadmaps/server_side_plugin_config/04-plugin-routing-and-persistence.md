# Plugin Routing And Persistence

Metric queue routing is server-owned. Plugin authors describe metric behavior,
inputs, outputs, and entrypoints. They do not decide job dispatch queues.

## Manifest Changes

`lyra.plugin.json` must stop requiring `queue`.

Implementation should remove queue from the schema v3 authoring model and from
compiled metric contracts unless a runtime-only resolved queue field is still
useful inside the application. Public SDK docs and plugin examples must be
updated to remove queue ownership from plugin author instructions.

If existing manifests still contain `queue`, the new implementation may reject
the extra field according to the strict manifest policy. No compatibility path
is required.

## Assignment Source

Metric queue assignments live in:

```toml
[plugins.metric_queues]
metric_name = "queue_name"
```

Each assigned queue must be listed in:

```toml
[plugins]
allowed_queues = ["interactive", "batch"]
```

New assignments use:

```toml
[plugins]
default_queue = "interactive"
```

`plugins.default_queue` must also appear in `plugins.allowed_queues`.

## API Catalog Refresh

The API owns assignment persistence. During catalog refresh:

1. Sync plugin repositories listed in `plugins.repos`.
2. Parse plugin manifests without importing plugin code.
3. Collect discovered metric names.
4. Load existing `[plugins.metric_queues]`.
5. For each discovered metric without an assignment, assign
   `plugins.default_queue`.
6. Persist any new assignments back to `/lyra_data/config/lyra.toml`.
7. Build the API registry using the resolved queue for each metric.

Assignment persistence must happen before the refreshed registry is exposed to
job creation. If the TOML write fails, refresh must fail and the previous
registry should remain in effect.

The API may leave assignments for metrics that are no longer discovered. Stale
assignments are harmless and help if a plugin is temporarily unavailable or
later restored.

## Job Dispatch

`POST /jobs` must dispatch `lyra.run_metric` to the queue resolved from server
config, not from the plugin manifest.

The job envelope does not need to include the queue. Queue is a server dispatch
decision made at submission time.

## Worker Loading

Workers read, but do not mutate, metric assignments.

At startup, a worker process receives a worker name and loads
`[workers.<name>]`. It then:

1. Syncs and installs plugin repositories into that worker's `install_dir`.
2. Parses plugin manifests.
3. Resolves each metric's queue from `[plugins.metric_queues]`.
4. Imports only metrics whose resolved queue appears in the worker's `queues`.
5. Starts Celery consuming the same queues listed in the worker config.

If a discovered metric has no assignment on a worker, startup should fail with a
clear message that the API catalog refresh must run first. Workers must not
create assignments because that would allow concurrent worker processes to race
on the config file.

