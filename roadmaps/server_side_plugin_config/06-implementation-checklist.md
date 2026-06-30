# Implementation Checklist

This checklist is ordered for implementation. Complete each section before
moving to the next one.

## 1. Config Models And Loader

- Add typed config models for the `schema_version = 1` TOML contract.
- Add a central loader that reads `/lyra_data/config/lyra.toml` with `tomllib`.
- Add secret file resolution helpers for scalar secrets and service account
  paths.
- Add atomic TOML write support for metric queue assignment persistence.
- Add tests for valid config, missing config, invalid TOML, unknown fields,
  invalid values, and missing or empty secret files.

## 2. Replace Runtime Env Reads

- Update Redis, Celery, database, Earth Engine, admin auth, logging, job store,
  plugin sync, and API startup code to consume typed config.
- Keep only bootstrap process selection outside TOML.
- Add tests proving runtime modules use config values rather than environment
  variables.

## 3. Move Metric Queues To Server Config

- Remove `queue` from `lyra.plugin.json` authoring requirements.
- Resolve metric queues from `[plugins.metric_queues]`.
- During API catalog refresh, auto-assign new metrics to
  `plugins.default_queue` and persist the TOML file.
- Ensure workers read assignments but never write them.
- Add tests for API dispatch queues, new metric assignment persistence, stale
  assignments, invalid queue names, and worker filtering.

## 4. Worker Bootstrap

- Add a worker startup path that accepts a worker name.
- Load `[workers.<name>]` and derive Celery queues and concurrency from config.
- Fail clearly for missing or unknown worker names.
- Ensure each worker uses its configured plugin install directory and temp
  directory.

## 5. Docker And Docs

- Update Compose files to use one `lyra_data` volume mounted at `/lyra_data`.
- Remove separate plugin catalog and per-worker plugin volumes.
- Remove runtime app environment variables from Compose.
- Update public docs after implementation:
  - Deployment
  - Local development
  - Plugin manifests
  - Plugin quickstart
  - Plugin author checklist
  - Reference
  - Architecture
  - Runner plugins

## Acceptance Criteria

Implementation is complete when:

- A fresh deployment can start API and workers from `/lyra_data/config/lyra.toml`.
- No Lyra app module reads old app settings directly from environment variables.
- Plugin authors no longer declare queues.
- New plugin metrics receive persisted queue assignments during API catalog
  refresh.
- API job dispatch and worker imports agree on the queue for every metric.
- Queue assignments survive process restarts and plugin repository updates.
- Compose uses one `lyra_data` volume for config, cache, plugins, secrets, and
  logs.
- The test suite covers config parsing, secret references, dispatch routing,
  worker filtering, Docker layout expectations, and manifest queue removal.

