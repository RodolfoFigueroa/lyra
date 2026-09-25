---
title: Architecture
description: Understand catalog discovery, authenticated submission, and warm worker execution.
---

Lyra separates public contracts from trusted execution.

## Catalog path

1. The deployment image installs Lyra and its plugins from a locked dependency set.
2. Operators select installed distributions and queue assignments in `lyra.toml`.
3. At startup the API reads installed format-5 manifests without importing plugin code.
4. The registry validates the generated request schemas and spatial metadata
   directly; there is no second compiled manifest.
5. `/metrics` publishes only client-facing names, descriptions, schemas, spatial
   mappings, outputs, and a contract fingerprint.

The public fingerprint excludes distribution names, factories, queues, and job
state.

API and workers independently validate installed distribution versions and manifests.
Workers import factories only for plugins serving their queues and compare the live
definitions with those manifests. They can start before the API. No shared catalog
files or runtime package installation are needed.

## Job path

1. An authenticated caller submits a metric, input, and idempotency key.
2. The API validates the current metric schema and resolves spatial wrappers.
3. It enforces shared rate limits, captures immutable provenance, stores queued
   state, and dispatches `lyra.run_metric` to the assigned queue.
4. A warm worker parses the envelope through the imported `PluginDefinition`,
   constructs the declared parameter model (applying defaults and semantic
   validators), supplies resolved geometry and optional `RunContext`, and calls
   the metric.
5. The worker validates native DataFrame/Path returns through the shared SDK
   normalizer and stores progress, status, and a terminal transport result.
6. Clients poll status, inspect descriptors, and download retained
   output.

## Components

| Component | Responsibility |
| --- | --- |
| FastAPI application | Discovery, validation, resolution, submission, result access, and administration. |
| `lyra-sdk` | Shared plugin, geometry, catalog, job, and runtime contracts. |
| `lyra-api` | Sync and async HTTP clients. |
| Celery workers | Trusted plugin import, execution, and result validation. |
| Redis | Celery transport plus retained job status, events, provenance, and results. |
| PostGIS | Readiness and database-backed spatial resolution. |
| MCP adapter | Strict agent tools over the same submission and result services. |

API and worker processes deliberately have different trust boundaries. A valid
catalog entry proves a manifest is readable; it does not prove a worker can
import or execute the plugin.

The API owns one database runtime, starts it during application lifespan, and
closes it during shutdown or startup failure. REST dependencies and the in-process
MCP backend receive that same runtime explicitly. Spatial resolution uses its
bounded executor and an engine-bound converter map; there is no global converter
fallback. Explicit GeoJSON inputs follow this execution path without opening a
database connection. Tests that construct applications or submit jobs directly
must supply the runtime; standalone MCP transport tests supply a backend.
