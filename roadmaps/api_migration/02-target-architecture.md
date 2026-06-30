# Target Architecture

## Config Split

Lyra has two durable configuration layers.

`/lyra_data/config/lyra.toml` is operator-authored deployment config. It is
mounted read-only in Docker.

`/lyra_data/state/plugins.toml` is Lyra-owned operational state. It is written by
Lyra and lives inside the writable `lyra_data` volume.

## What Stays In `lyra.toml`

The read-only config file keeps deployment policy and infrastructure settings:

- `[api]`: API host and port.
- `[redis]`: Redis URL.
- `[database]`: database host, port, name, user, and password file path.
- `[earth_engine]`: Earth Engine project and service account file path.
- `[admin]`: admin API key file path.
- `[logging]`: log level and optional log file.
- `[job_store]`: job status/result/event TTL.
- `[plugins]`: plugin runtime paths, `default_queue`, and `allowed_queues`.
- `[workers.<name>]`: queues, concurrency, install directory, and temp directory.

The `[plugins]` table remains because queue policy and runtime paths are
deployment-level concerns.

## What Leaves `lyra.toml`

Remove these from the operator-authored config:

- `[plugins].repos`
- `[plugins.metric_queues]`

No compatibility layer is required. A config file that still contains these
fields should fail validation as an unknown-field error after the migration.

## What Moves To Lyra-Owned State

`/lyra_data/state/plugins.toml` owns:

- plugin repo inventory,
- repo enabled/disabled state,
- repo refs or pins,
- metric queue assignments.

Lyra creates this file if it does not exist. Operators may inspect it, but admin
API endpoints are the canonical mutation interface.

## Admin API Role

Admin endpoints become the operational interface for plugin management.

They must:

- use existing bearer admin authentication,
- validate inputs before mutating state,
- write state atomically,
- return clear errors for invalid repos or queues,
- keep worker restart explicit after repo or routing changes.

