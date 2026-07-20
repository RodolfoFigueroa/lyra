---
title: Publish and Debug Plugins
description: Validate, register, route, update, and troubleshoot a trusted plugin.
---

Worker containers install and execute plugin code with their own permissions.
Only configure sources you trust, and scope worker secrets, mounts, and network
access accordingly.

## Preflight

Before publishing:

- make the repository an installable Python package;
- declare every directly imported dependency;
- configure `[tool.lyra].plugin` inside the installed package;
- generate and commit `lyra.plugin.json`;
- unit-test decorated functions with typed arguments and a fake `RunContext`;
- test at least one registry-adapter call with a resolved envelope;
- assert job IDs, indices, columns, media types, and batch expansion;
- run `lyra-plugin check-manifest` in plugin CI.

## Source forms

| Form | Behavior |
| --- | --- |
| `owner/repo[@ref]` | Clone a GitHub repository and optional branch or tag. |
| `https://github.com/owner/repo[@ref]` | Equivalent explicit GitHub form. |
| `file:///absolute/repository` | Clone committed local Git state. |
| `dir:///absolute/directory` | Copy a development snapshot, including uncommitted files. |

Raw filesystem paths are rejected. Local sources do not accept refs. Container
deployments must mount local sources at the same absolute path in the API and
every worker.

## Connect and route

Add or update sources through `/admin/plugin-repos`. Refresh the API catalog,
inspect `/admin/plugin-routing`, assign non-default queues where needed, then
restart worker pools. Workers install plugin code only at startup and do not
hot-reload it in process.

The API may expose a valid manifest even when a worker cannot install or import
the package. Always run a worker consuming the metric's assigned queue and read
its startup logs.

## Diagnose

| Symptom | Likely cause |
| --- | --- |
| Metric absent from `/metrics` | Source unreachable, root manifest missing/invalid, or catalog not refreshed. |
| Worker fails during startup | Packaging, installation, import, duplicate name, or stale manifest failure. |
| Job stays queued | No live worker consumes the assigned queue. |
| Job reports unknown metric | Worker skipped or could not load the plugin. |
| Submission returns `422` | Input differs from the live metric schema. |
| Spatial resolution returns `503` | PostGIS is unavailable or lacks required spatial data. |
| Worker reports invalid result | Job ID, index, columns, file path, or output kind violates the declaration. |

Use the admin catalog, routing, worker, and queue views together; the API
catalog alone cannot prove executable worker state.
