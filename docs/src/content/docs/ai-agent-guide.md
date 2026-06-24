---
title: AI Agent Guide
description: Stable source map, contracts, commands, and current behavior for AI agents working on Lyra.
---

This page is optimized for agents that need to inspect, edit, or reason about Lyra.

## Current Public Behavior

Lyra exposes current execution through:

- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/events`
- `GET /jobs/{job_id}/result`

Supporting routes:

- `GET /metrics`
- `GET /metrics/{metric_name}`
- `GET /data_types` for grouped `location` and `bounds` wrapper schemas
- `GET /met_zone_code`
- `POST /update-plugins`

## Source Map

| Task | Start Here |
| --- | --- |
| Public job routes | `lyra_app/routes/jobs.py` |
| Metric catalog and payload validation | `lyra_app/registry.py` |
| Redis job status, result, and event store | `lyra_app/job_store.py` |
| Generic Celery worker execution | `lyra_app/worker.py` |
| Plugin sync and install helpers | `lyra_app/plugins.py` |
| Admin plugin refresh route | `lyra_app/routes/admin.py` |
| SDK job and API models | `packages/lyra_sdk/src/lyra/sdk/models/job.py` |
| SDK manifest models | `packages/lyra_sdk/src/lyra/sdk/models/plugin_v2.py` |
| Runner context protocol | `packages/lyra_sdk/src/lyra/sdk/context.py` |
| Sync Python client | `packages/lyra_api/src/lyra/api/client/sync.py` |
| Async Python client | `packages/lyra_api/src/lyra/api/client/async_.py` |
| Compose deployment examples | `docker/docker-compose.yml` and `docker/docker-compose-dev.yml` |
| Documentation site | `docs/` |

## Contracts

Plugin manifests are `PluginManifestV2` with integer `schema_version: 2`.

Metric entrypoints are sync functions shaped as:

```python
def run(job: JobEnvelope, context: RunContext) -> JobResult:
    ...
```

The only Celery task name for metric execution is `lyra.run_metric`.

`JobResult.status` is terminal: `succeeded`, `failed`, or `cancelled`.

Job lifecycle status can be `queued`, `started`, `progress`, `succeeded`, `failed`, or `cancelled`.

For spatial plugins, read [Spatial Plugin Inputs](../spatial-plugin-inputs/).
Every metric manifest declares required `spatial_inputs`. The API injects
wrapper schemas into `/metrics`, validates client wrappers, and resolves them
to GeoJSON dictionaries before workers receive `JobEnvelope.input`.

## Commands

Prefer focused commands while iterating:

```bash
uv run pytest tests/test_jobs_route.py
uv run ty check
uv run ruff check path/to/file.py
npm run check --prefix docs
npm run build --prefix docs
```

Run broader checks before handoff:

```bash
uv run pytest
uv run ty check
uv run ruff check
npm audit --prefix docs
```

## Editing Expectations

Keep docs and public contracts synchronized. If route behavior, SDK models, plugin manifest fields, worker execution, or client behavior changes, update the relevant docs in the same change.

Use source code as authority over examples. Replace placeholder metric names and payloads with values from the active `/metrics` catalog when testing a live deployment.

Do not infer runtime plugin availability from this repository alone. Metrics come from repositories listed in `LYRA_PLUGIN_REPOS`.
