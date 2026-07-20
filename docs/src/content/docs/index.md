---
title: Lyra
description: Run typed spatial metrics through a schema-driven job API.
---

Lyra turns independently maintained Python metric plugins into a discoverable,
authenticated job API. A plugin declares typed inputs and outputs; Lyra exposes
the resulting schema, resolves spatial inputs, dispatches work to warm Celery
workers, and retains status, events, provenance, and results for a configured
time.

## Choose a path

- **Run Lyra:** follow the [quickstart](./quickstart/).
- **Call Lyra:** use the [REST API](./use/rest-api/) or
  [Python client](./use/python-client/).
- **Publish a metric:** start with the [plugin quickstart](./plugins/quickstart/).
- **Deploy Lyra:** read [deployment](./operate/deployment/) and the
  [operator runbook](./operate/runbook/).
- **Inspect exact contracts:** use the [generated reference](./reference/).

## Core model

API processes read generated plugin manifests without importing plugin code.
Workers install trusted plugin packages, import their `PluginDefinition`, and
execute typed functions through the single `lyra.run_metric` task. Redis carries
Celery traffic and retained job state; PostGIS resolves database-backed spatial
wrappers.

Public routes expose health, metric schemas, and lookups. Every `/jobs` route
and the MCP transport require the agent key. Every `/admin` route requires the
separate admin key.
