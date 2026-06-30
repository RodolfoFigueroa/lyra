# Current State And Goals

Lyra currently uses several configuration channels at once. That works for
local development, but it splits one deployment decision across code, Compose,
plugin manifests, and operator-provided environment variables.

This roadmap replaces that split with a single server-side TOML config file.

## Current State

Current application settings are spread across:

- Environment variables read by app modules:
  - `CELERY_BROKER_URL`
  - `EARTHENGINE_PROJECT`
  - `LYRA_ADMIN_API_KEY`
  - `LYRA_CACHE_DIR`
  - `LYRA_JOB_STORE_TTL_SECONDS`
  - `LYRA_LOG_FILE`
  - `LYRA_LOG_LEVEL`
  - `LYRA_PLUGIN_CATALOG_DIR`
  - `LYRA_PLUGIN_INSTALL_DIR`
  - `LYRA_PLUGIN_REPOS`
  - `LYRA_PORT`
  - `LYRA_RUNNER_QUEUES`
  - `LYRA_RUNNER_TEMP_DIR`
  - `POSTGRES_DB`
  - `POSTGRES_HOST`
  - `POSTGRES_PASSWORD`
  - `POSTGRES_PORT`
  - `POSTGRES_USER`
- Fixed file paths in code:
  - Earth Engine credentials are expected at `/app/service-account.json`.
- Plugin manifests:
  - Each metric currently declares its Celery queue in `lyra.plugin.json`.
- Docker Compose:
  - API and workers mount separate plugin volumes.
  - Workers define queue membership through `LYRA_RUNNER_QUEUES` and Celery
    `-Q` arguments.
  - Cache and service account paths are controlled by host-side environment
    variables.

## Problems

The current design makes plugin authors responsible for a deployment concern:
which queue a metric should run on. It also makes queue routing fragile because
the API dispatch queue, worker import filter, and Celery queue subscription are
configured through separate channels.

The mixed config model also makes restarts and git pulls awkward. A metric's
queue assignment should survive plugin repository updates, but plugin manifests
are owned by plugin authors and may change when repos are updated.

## Goals

The implementation must:

- Use `/lyra_data/config/lyra.toml` as the runtime config source of truth.
- Use a single Docker volume named `lyra_data`.
- Store durable app data under `/lyra_data`.
- Keep secret values out of TOML by using file references.
- Move plugin metric queue ownership from plugin manifests to server config.
- Persist metric queue assignments across app restarts and plugin git pulls.
- Let the API and workers resolve queues from the same config contract.
- Remove `queue` from `lyra.plugin.json` authoring requirements.
- Replace direct `os.environ` app setting reads with typed config access.

## Non-Goals

The implementation does not need to:

- Preserve old environment-variable behavior.
- Support old plugin manifests that require or depend on `queue`.
- Provide schema migrations.
- Keep the old per-worker plugin Docker volumes.

