---
title: Contributor Guide
description: How to navigate the Lyra repository and make source changes safely.
---

This page is for developers changing Lyra itself.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `lyra_app/` | FastAPI routes, plugin registry, Redis job store, Celery worker, deployment runtime helpers. |
| `packages/lyra_sdk/` | Public SDK contracts for manifests, jobs, metrics, and runner context typing. |
| `packages/lyra_api/` | Sync and async Python clients for the HTTP job API. |
| `packages/lyra_utils/` | Shared utilities used by Lyra and plugin code. |
| `docker/` | Compose deployment examples for API, Redis, and warm worker pools. |
| `tests/` | Unit tests for routes, SDK contracts, registry, job store, workers, clients, and deployment behavior. |
| `docs/` | Astro Starlight documentation site. |

## Common Source Areas

Use `lyra_app/routes/jobs.py` for public job route behavior.

Use `lyra_app/registry.py` for manifest catalog loading, metric lookup, request validation, and catalog fingerprinting.

Use `lyra_app/worker.py` for generic Celery task execution and runner plugin loading.

Use `lyra_app/job_store.py` for Redis status, result, and stream event operations.

Use `packages/lyra_sdk/src/lyra/sdk/models/` when changing public contracts.

Use `packages/lyra_api/src/lyra/api/client/` when changing client behavior.

## Change Discipline

Keep API catalog behavior separate from worker execution behavior. The API reads manifest metadata and validates requests; workers install and import plugin code.

When changing public contracts, update the SDK models, route/client behavior, tests, and docs together.

When changing job lifecycle behavior, update route tests, job store tests, worker tests, and client expectations together.

Docs are part of the product. If a behavior is visible to plugin authors, API clients, deployers, or agents, update the Starlight docs in the same change.

## Useful Searches

Find public routes:

```bash
rg "@router\\." lyra_app/routes
```

Find SDK contracts:

```bash
rg "class Job|class Plugin|class Metric" packages/lyra_sdk/src/lyra/sdk
```

Find job store behavior:

```bash
rg "job:\\{job_id\\}|JobEvent|JobResult" lyra_app tests
```
