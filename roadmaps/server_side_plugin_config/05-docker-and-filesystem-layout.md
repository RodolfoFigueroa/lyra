# Docker And Filesystem Layout

All durable Lyra server files should live under one Docker volume.

## Volume

Use one named Docker volume:

```yaml
volumes:
  lyra_data:
    name: lyra_data
```

Mount it into every Lyra API and worker container at:

```text
/lyra_data
```

Redis may keep its own storage decision separate. This roadmap concerns Lyra
application config, plugins, cache, secrets, and logs.

## Directory Tree

The implementation should use this layout:

```text
/lyra_data/
  config/
    lyra.toml
  cache/
    jobs/
      interactive/
      batch/
  plugins/
    catalog/
    runners/
      interactive/
      batch/
  secrets/
    admin_api_key
    postgres_password
    service-account.json
  logs/
    lyra.log
```

Required directories should be created by startup code when safe. Secret files
must not be created automatically because empty generated secrets can hide
deployment mistakes.

## Compose Shape

Compose should mount only `lyra_data` into Lyra app containers. The separate
plugin catalog and per-worker plugin volumes should be removed.

The API service should:

- Mount `lyra_data:/lyra_data`.
- Start with `/lyra_data/config/lyra.toml`.
- No longer receive `LYRA_PLUGIN_REPOS`, `LYRA_PLUGIN_CATALOG_DIR`,
  `LYRA_ADMIN_API_KEY`, `LYRA_PORT`, `LYRA_LOG_LEVEL`, or similar app settings
  as environment variables.

Each worker service should:

- Mount `lyra_data:/lyra_data`.
- Pass a worker name as a command argument.
- Use `[workers.<name>]` for queues, concurrency, install directory, and temp
  directory.

The Celery command must consume the queues declared in TOML. The implementation
may use a small worker entrypoint wrapper that reads config and launches Celery
with the correct `-Q` and concurrency values.

## Local Development

Local development should use the same config contract. Developers should create
or mount a local `lyra_data` directory with:

```text
lyra_data/config/lyra.toml
lyra_data/secrets/admin_api_key
lyra_data/secrets/postgres_password
lyra_data/secrets/service-account.json
```

Docs should stop instructing users to configure application behavior through a
large `.env` file. Host-side environment variables may still be used by Compose
only to locate a bind-mounted host directory if needed, but runtime app settings
belong in TOML.

