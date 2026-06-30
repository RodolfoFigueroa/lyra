# Current State And Problems

## Current State

Lyra currently uses `/lyra_data/config/lyra.toml` for both deployment config and
plugin operational state.

Current plugin-related config lives under `[plugins]`:

```toml
[plugins]
repos = ["owner/plugin-repo@main"]
catalog_dir = "/lyra_data/plugins/catalog"
runner_base_dir = "/lyra_data/plugins/runners"
default_queue = "interactive"
allowed_queues = ["interactive", "batch"]

[plugins.metric_queues]
walkability_score = "interactive"
regional_accessibility = "batch"
```

The current runtime behavior is:

- API catalog refresh reads `plugins.repos`.
- Catalog sync clones or updates repos under `plugins.catalog_dir`.
- Missing metrics are auto-assigned to `plugins.default_queue`.
- Auto-created assignments are written back into `lyra.toml`.
- Workers read the same repo list and metric queue assignments.
- The only admin operation is `POST /update-plugins`, which refreshes the
  catalog and asks workers to restart.

## Problems

This is awkward for the desired Docker deployment model.

`lyra.toml` cannot be a simple read-only file mount if Lyra writes metric queue
assignments back to it.

Adding or removing plugin repos requires editing deployment config. For Docker
operators, that means changing mounted config files and often restarting more of
the stack than should be necessary.

Plugin repo inventory and metric routing are operational state. They change
inside a running deployment and should be managed by Lyra APIs. Redis settings,
database settings, worker pool shape, queue policy, and secret file paths are
deployment config and should remain in read-only TOML.

## Desired Change

Move plugin repo inventory and metric queue assignments out of `lyra.toml`.

Make admin API endpoints the normal way to:

- add plugin repos,
- remove plugin repos,
- enable or disable plugin repos,
- pull plugin repos,
- refresh the plugin catalog,
- view metric routing,
- set metric routing,
- delete metric routing assignments.

Lyra should persist those changes in `/lyra_data/state/plugins.toml`.

