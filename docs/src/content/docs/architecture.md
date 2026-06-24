---
title: Architecture
description: How Lyra routes requests from manifest metadata to warm workers and Redis-backed job records.
---

Lyra separates catalog metadata, public job submission, and runner execution.

## Main Components

`lyra_app` is the FastAPI application and worker runtime.

`packages/lyra_sdk` contains public Pydantic contracts for manifests, jobs, metric metadata, and runner context typing.

`packages/lyra_api` contains sync and async Python clients for the public HTTP API.

`packages/lyra_utils` contains shared utility code used by Lyra and metric implementations.

Redis is used for Celery transport and for job status, result, and event storage.

## Catalog Flow

1. `LYRA_PLUGIN_REPOS` lists plugin GitHub repositories.
2. The API syncs those repositories into `LYRA_PLUGIN_CATALOG_DIR`, defaulting to `/lyra_plugin_catalog`.
3. Each repository must contain `lyra.plugin.json`.
4. `lyra_app.registry` parses each manifest as `PluginManifestV2`.
5. The registry builds an effective `request_schema` by injecting canonical
   spatial wrapper schemas for each metric's required `spatial_inputs`.
6. `/metrics` exposes only `name`, `description`, the effective
   `request_schema`, and optional `result_schema`.

The API catalog does not import plugin Python code.

## Job Flow

1. A client submits `POST /jobs` with `metric`, `input`, and optional `idempotency_key`.
2. The API validates `input` against the selected metric's effective `request_schema`.
3. The API resolves each declared spatial wrapper into canonical GeoJSON.
4. The API creates a `JobEnvelope`, stores a queued job snapshot, and dispatches `lyra.run_metric` to the metric's manifest queue.
5. A worker consuming that queue validates the envelope, builds a `RunContext`, and calls the metric entrypoint.
6. The worker stores progress events, terminal status, and a normalized `JobResult`.
7. Clients read status, stream events, and fetch results through the `/jobs/{job_id}` routes.

## Worker Flow

Workers sync plugin repositories into `LYRA_PLUGIN_INSTALL_DIR`, defaulting to `/lyra_plugins`. They check install compatibility, install plugins editable into the worker Python environment, parse v2 manifests, and import only metrics whose `execution.queue` matches `LYRA_RUNNER_QUEUES`.

All metric execution goes through one Celery task name: `lyra.run_metric`.

## Redis Job Store

For each job, Lyra writes:

- `job:{job_id}:status`
- `job:{job_id}:result`
- `job:{job_id}:events`

The status key stores lifecycle state. The result key stores the terminal `JobResult`. The events key is a Redis Stream consumed by the SSE route.

## Public Contracts

The stable contracts to read first are:

- `PluginManifestV2` for plugin metadata.
- `MetricInfoV2` for `/metrics` responses.
- `JobCreateRequest`, `JobCreateResponse`, `JobStatusInfo`, `JobEvent`, and `JobResult` for public job APIs.
- `JobEnvelope` and `RunContext` for runner plugins.
