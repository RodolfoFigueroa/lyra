---
title: Architecture
description: Understand catalog compilation, authenticated submission, and warm worker execution.
---

Lyra separates public contracts from trusted execution.

## Catalog path

1. Operators configure plugin sources and queue assignments in Lyra-owned state.
2. The API syncs enabled sources into its catalog directory.
3. It parses committed schema-v3 manifests without importing Python code.
4. The registry compiles semantic inputs into effective request schemas and
   runtime spatial/batch metadata.
5. `/metrics` publishes only client-facing names, descriptions, schemas, spatial
   mappings, outputs, and a contract fingerprint.

The public fingerprint excludes repository IDs, entrypoints, queues, and job
state.

## Job path

1. An authenticated caller submits a metric, input, and idempotency key.
2. The API validates the current metric schema and resolves spatial wrappers.
3. It enforces shared rate limits, captures immutable provenance, stores queued
   state, and dispatches `lyra.run_metric` to the assigned queue.
4. A warm worker parses the envelope through the imported `PluginDefinition`,
   creates typed arguments and `RunContext`, and calls the metric.
5. The worker validates and stores progress, status, and a normalized terminal
   result.
6. Clients poll or stream events, inspect descriptors, and download retained
   output.

## Components

| Component | Responsibility |
| --- | --- |
| FastAPI application | Discovery, validation, resolution, submission, result access, and administration. |
| `lyra-sdk` | Shared plugin, geometry, catalog, job, and runtime contracts. |
| `lyra-api` | Sync and async HTTP clients. |
| Celery workers | Trusted plugin installation, import, execution, and result validation. |
| Redis | Celery transport plus retained job status, events, provenance, and results. |
| PostGIS | Readiness and database-backed spatial resolution. |
| MCP adapter | Strict agent tools over the same submission and result services. |

API and worker processes deliberately have different trust boundaries. A valid
catalog entry proves a manifest is readable; it does not prove a worker can
install or execute the plugin.
