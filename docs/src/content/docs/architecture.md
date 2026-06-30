---
title: Architecture
description: How Lyra routes requests from manifest metadata to warm workers and Redis-backed job records.
---

Lyra separates catalog metadata, public job submission, and runner execution.

## Main Components

`lyra_app` is the FastAPI application and worker runtime.

`packages/lyra_sdk` contains public Pydantic contracts for manifests, jobs,
metric metadata, and runner context typing.

`packages/lyra_api` contains sync and async Python clients for the public HTTP API.

`packages/lyra_utils` contains shared utility code used by Lyra and metric implementations.

Redis is used for Celery transport and for job status, result, and event storage.

## Catalog Flow

1. `/lyra_data/state/plugins.toml` lists plugin sources: GitHub entries,
   explicit `file://` local git repositories, or development `dir://` directory
   snapshots.
2. The API syncs enabled plugin sources into `plugins.catalog_dir`, usually
   `/lyra_data/plugins/catalog`.
3. Each synced source must contain `lyra.plugin.json`.
4. `lyra_app.registry` parses each manifest as `PluginManifestV3`.
5. The registry compiles each metric's semantic `inputs` into an effective
   `request_schema`, spatial runtime metadata, and batch runtime metadata.
6. Missing metric queue assignments are added to plugin state using
   `plugins.default_queue`.
7. `/metrics` exposes only `name`, `description`, the effective
   `request_schema`, and the `output` declaration.

The API catalog does not import plugin Python code.

## Job Flow

1. A client submits `POST /jobs` with `metric`, `input`, and optional `idempotency_key`.
2. The API validates `input` against the selected metric's effective `request_schema`.
3. The API resolves each declared spatial wrapper into canonical GeoJSON.
4. The API creates a `JobEnvelope`, stores a queued job snapshot, and dispatches `lyra.run_metric` to the metric's server-assigned queue.
5. A worker consuming that queue validates the envelope, builds a `RunContext`, and calls the metric entrypoint.
6. The worker stores progress events, terminal status, and a normalized terminal result.
7. Clients read status, stream events, and fetch results through the `/jobs/{job_id}` routes.

## Worker Flow

Workers start with `python -m lyra_app.worker_launcher <worker-name>`. The
launcher reads `[workers.<name>]`, loads plugin sources and routing from
`/lyra_data/state/plugins.toml`, syncs enabled sources into the worker's
install directory under `/lyra_data/plugins/runners`, parses schema v3
manifests, compiles them, imports metrics assigned to that worker's queues, and
starts Celery with matching `-Q` and concurrency values.

All metric execution goes through one Celery task name: `lyra.run_metric`.

## Redis Job Store

For each job, Lyra writes:

- `job:{job_id}:status`
- `job:{job_id}:result`
- `job:{job_id}:events`

The status key stores lifecycle state. The result key stores the terminal result. The events key is a Redis Stream consumed by the SSE route.

## Public Contracts

The stable contracts to read first are:

- `PluginManifestV3` for plugin metadata.
- `MetricInfoV3` for `/metrics` responses.
- `JobCreateRequest`, `JobCreateResponse`, `JobStatusInfo`, `JobEvent`, and terminal result models for public job APIs.
- `JobEnvelope` and `RunContext` for runner plugins.
