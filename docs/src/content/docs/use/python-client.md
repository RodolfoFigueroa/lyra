---
title: Python Client
description: Generate a typed Lyra client and use sync, async, or raw resources.
---

Generated clients are the primary Python interface to Lyra metrics. Commit the
catalog snapshot and generated source with your application so code review and
type checking cover every metric call.

## Pull and generate

```bash
uv run lyra-client catalog pull \
  --host lyra.example.com \
  --output lyra-catalog.json

uv run lyra-client generate \
  --catalog lyra-catalog.json \
  --package acme_lyra \
  --output src/acme_lyra
```

Catalog snapshots contain no timestamps or machine-specific metadata. The
generator writes deterministic source, a contract module, `py.typed`, and a
manifest of files it owns. It preserves unrelated files in the destination.

Use check mode in CI. It performs no writes and prints a concise diff when the
committed package is stale:

```bash
uv run lyra-client generate \
  --catalog lyra-catalog.json \
  --package acme_lyra \
  --output src/acme_lyra \
  --check
```

## Synchronous use

```python
import os

from acme_lyra import Client, MetZoneCode
from lyra.api import RunOptions

client = Client(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)

result = client.metrics.job_accessibility.run(
    location=MetZoneCode(value="09.01"),
    limit=50,
    lyra_options=RunOptions(
        idempotency_key="accessibility-2026-07",
        timeout=300,
    ),
)
```

Metric fields are keyword-only parameters with the catalog's required,
nullable, and default semantics. Generated request models are exported as
`<MetricPascalCase>Request`. Pydantic validation and the complete Draft 2020-12
request schema run locally before any network request.

Every metric has `submit()` and `run()`. Submission returns a typed `JobHandle`
with `status()`, `events()`, `result()`, and `wait()`. File metrics additionally
have `run_to_file(path=...)`. Failed and cancelled terminal states raise
`MetricRunError` with the job ID, status, structured error, and terminal result.

## Asynchronous use

```python
from acme_lyra import AsyncClient, CVEGEOList

client = AsyncClient("lyra.example.com", agent_api_key="...")
result = await client.metrics.population.run(
    location=CVEGEOList(value=["09"]),
)
```

Await generated `submit()`, `run()`, `run_to_file()`, handle `wait()` and
`result()`, and ordinary async core-resource operations. Consume handle events
with `async for`.

## Catalog compatibility

Generated clients verify the live catalog lazily before the first metric
submission. `verify_catalog="warn"` is the default: a mismatch or failed
verification emits `CatalogCompatibilityWarning` and continues. Use `"error"`
to raise `CatalogCompatibilityError`, or `"off"` to make no verification
request. Server-side request validation remains authoritative.

## Core resources and raw escape hatch

Generated clients expose the core namespaces alongside `metrics`: `health`,
`lookups`, `catalog`, `jobs`, `results`, `raw`, and `admin`. For a metric not in
the snapshot, use the explicitly untyped escape hatch:

```python
result = client.raw.run(
    "new_metric",
    {"location": {"data_type": "met_zone_code", "value": "09.01"}},
)
```

Raw arguments are JSON objects and raw successful results are
`TableJobResult | FileJobResult`. Prefer regeneration once the new catalog is
available.

Operator workflows live under `client.admin`, including `admin.jobs`,
`admin.plugin_repos`, `admin.catalog`, `admin.workers`, `admin.queues`, and
`admin.routing`. Use a separate client configured with `admin_api_key` for
operator applications.
