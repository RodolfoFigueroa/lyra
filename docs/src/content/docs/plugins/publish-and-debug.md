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
- configure `[tool.lyra].factory` inside the installed package;
- generate and commit `lyra.plugin.json`;
- unit-test decorated functions with typed arguments and a fake `RunContext`;
- test at least one registry-adapter call with a resolved envelope;
- assert job IDs, indices, columns, media types, and batch expansion;
- run `lyra-plugin check-manifest` in plugin CI.

## Source forms

| Form | Behavior |
| --- | --- |
| `owner/repo` | Clone a GitHub repository and a separate optional `ref` (branch, tag, or commit). |
| `https://github.com/owner/repo` | Equivalent explicit GitHub form. |
| `file:///absolute/repository` | Clone committed local Git state. |
| `dir:///absolute/directory` | Copy a development snapshot, including uncommitted files. |

Raw filesystem paths are rejected. `file://` sources support Git refs; `dir://`
sources do not. Specify branches, tags, and commits in `ref`, never as an
`owner/repo@ref` suffix. GitHub forms may include a trailing `.git`; configuration
normalizes them to `owner/repo`.

Local URIs accept an empty host or `localhost`, which is normalized to an empty
host. Spaces and other special filename characters are percent-encoded in the
normalized URI. `@` is allowed in local filenames and never denotes a revision.
Queries and fragments are rejected; encode literal `?` and `#` filename characters
as `%3F` and `%23`. Configuration validation checks syntax without accessing local
paths or Git. Mount local sources in the API container.

Each API startup captures enabled sources into fresh staging directories. Git
sources capture the selected commit; directory sources copy current files while
excluding Git metadata, Python caches, virtual environments, and build artifacts.
Symlink contents are materialized in the captured tree; broken links fail startup.
The catalog becomes ready only after all sources and routing have been validated.
There is no incremental synchronization or refresh on catalog reads.

Workers verify the captured content hash and install private copies of that
snapshot, even if the original source subsequently changes. Restart the API and
workers together to capture source updates.

## Connect and route

Declare sources using `[[plugins.repos]]` in `lyra.toml`, with a stable `id`,
`source`, optional `ref`, and `enabled` flag. Put queue overrides under
`[plugins.repos.routing]`; other metrics use the default queue. Inspect effective
routing with `lyra-admin routing list`.

Drain pending and active work, edit the file, and restart the API and all worker
pools to apply changes. See [Deployment](../../operate/deployment/).

The API may expose a valid manifest even when a worker cannot install or import
the package. Always run a worker consuming the metric's assigned queue and read
its startup logs.

## Diagnose

| Symptom | Likely cause |
| --- | --- |
| Metric absent from `/metrics` | Source unreachable, root manifest missing/invalid, or repository disabled. |
| Worker fails during startup | Packaging, installation, import, duplicate name, or stale manifest failure. |
| Job stays queued | No live worker consumes the assigned queue. |
| Job reports unknown metric | API and worker deployments were not restarted together. |
| Submission returns `422` | Input differs from the live metric schema. |
| Spatial resolution returns `503` | PostGIS is unavailable or lacks required spatial data. |
| Worker reports invalid result | Job ID, index, columns, file path, or output kind violates the declaration. |

Use the admin catalog, routing, worker, and queue views together; the API
catalog alone cannot prove executable worker state.
