---
title: Publish and Debug Plugins
description: Validate, register, route, update, and troubleshoot a trusted plugin.
---

Worker containers execute installed plugin code with their own permissions.
Only install plugins you trust, and scope worker secrets, mounts, and network
access accordingly.

## Preflight

Before publishing:

- make the repository an installable Python package;
- declare every directly imported dependency;
- configure `[tool.lyra].factory` inside the installed package;
- generate and commit `lyra.plugin.json`;
- unit-test decorated functions with parameter models, resolved geometry, and a
  fake `RunContext` only when declared;
- exercise `PluginDefinition.prepare_parameters()` and `normalize_result()` locally;
- assert native DataFrame indices/columns or Path containment and media types;
- run `lyra-plugin check-manifest` in plugin CI.

## Package the manifest

Install plugins when building the deployment image. API and workers use the same
image and configuration. Runtime processes do not fetch sources or install packages.

For Hatch projects, include the generated root manifest as shared data:

```toml
[tool.hatch.build.targets.wheel.shared-data]
"lyra.plugin.json" = "share/lyra/plugins/my-plugin/lyra.plugin.json"

[tool.hatch.build.targets.sdist]
include = ["my_plugin", "lyra.plugin.json", "pyproject.toml"]
```

Replace `my-plugin` with the normalized distribution name: lowercase, with runs of
hyphens, underscores, and dots replaced by a single hyphen. The manifest's plugin
name must normalize to that distribution, and its version must match installed
package metadata. Lyra reads the file below the Python environment's `sys.prefix`.
See [Hatch shared data](https://hatch.pypa.io/latest/plugins/builder/wheel/#options).

## Connect and route

Add the plugin to the deployment project's dependencies, update its lockfile, and
build the image. Select installed packages in `lyra.toml`:

```toml
[[plugins.installed]]
distribution = "my-plugin"
enabled = true

[plugins.installed.routing]
expensive_metric = "batch"
```

Other metrics use `plugins.default_queue`. Inspect effective routing with
`lyra-admin routing list` and distribution versions with `lyra-admin plugins list`.
Disabled plugins are not loaded. An installed package is not automatically enabled.

Drain pending and active work before recreating the API and every worker with the
same image and configuration. See [Deployment](../../operate/deployment/).

## Develop locally

Use an editable install and mount the checkout at its installation path in each
container. Set the plugin's absolute `manifest_path` to the generated manifest in
that checkout. This override still requires the distribution to be installed.

- Implementation edits: restart the affected workers.
- Contract edits: regenerate the manifest, then restart API and workers.
- Dependency or version edits: update deployment locks and rebuild the image.
- Plugin selection or routing edits: restart API and workers.

The API may expose a valid manifest even when a worker cannot import its factory.
Workers compare the imported definition with the manifest and reject stale contracts.
Always run a worker consuming the metric's assigned queue and read its startup logs.

## Diagnose

| Symptom | Likely cause |
| --- | --- |
| Metric absent from `/metrics` | Distribution missing, manifest missing/invalid, or plugin disabled. |
| Worker fails during startup | Missing distribution, import, duplicate name, or stale manifest failure. |
| Job stays queued | No live worker consumes the assigned queue. |
| Job reports unknown metric | API and worker deployments were not restarted together. |
| Submission returns `422` | Input differs from the live metric schema. |
| Spatial resolution returns `503` | PostGIS is unavailable or lacks required spatial data. |
| Worker reports `invalid_input` | Python semantic validation rejected structurally valid parameters or defaults before the handler ran. |
| Worker reports `invalid_result` | Native return type, index, columns, values, or file path violates the declaration. |

Use the admin catalog, routing, worker, and queue views together; the API
catalog alone cannot prove executable worker state.
