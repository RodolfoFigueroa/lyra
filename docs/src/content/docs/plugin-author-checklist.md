---
title: Plugin Author Checklist
description: Validate, publish, connect, and troubleshoot a third-party Lyra plugin.
---

When your plugin works locally, use this checklist before you add its repository
through `/admin/plugin-repos`. If you are starting from an empty repo, begin
with [Plugin Quickstart](../plugin-quickstart/) and come back here before
publishing.

## Trust Model

Treat Lyra plugin sources as trusted code. API containers read only
`lyra.plugin.json` manifests, but worker containers sync plugin sources, run
`uv pip install`, install compatible packages editable, import matching
entrypoints, and execute plugin code with the worker container's permissions.

Configure only plugin sources you are willing to run inside the worker
environment. Keep worker secrets, network access, mounted volumes, and service
accounts scoped to what plugin code is allowed to use.

## Repository Checklist

- Configure `[tool.lyra].plugin` and commit the generated root `lyra.plugin.json`.
- Make the repository an installable Python package with `pyproject.toml`.
- Depend on `lyra-sdk` for runner contracts.
- Add `lyra-utils` only when plugin code uses GeoDataFrame, date, or Earth
  Engine helpers.
- Declare `geopandas`, `pandas`, or other libraries directly when plugin code
  imports them directly, even if another Lyra helper package also depends on
  them.
- Keep the configured `PluginDefinition` under the installed package, such as
  `example_plugin.metrics:plugin`.
- Import runtime contracts from `lyra-sdk` rather than from `lyra_app`.
- Choose the table, file, static column, or generated column shape with
  [Metric Output Design](../metric-output-design/).

## Repository Sources

`POST /admin/plugin-repos` accepts GitHub entries, explicit `file://` local git
repositories, and development `dir://` directory sources:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/plugin-b@main"}'
```

Supported forms are:

| Form | Meaning |
| --- | --- |
| `owner/repo` | Clone the repository's default branch. |
| `owner/repo@branch-or-tag` | Clone the named branch or tag. |
| `https://github.com/owner/repo` | Clone the repository's default branch with an explicit GitHub URL prefix. |
| `https://github.com/owner/repo@branch-or-tag` | Clone the named branch or tag with an explicit GitHub URL prefix. |
| `file:///absolute/path/to/repo` | Clone a local git repository from its current committed state. |
| `dir:///absolute/path/to/plugin` | Copy a development plugin directory snapshot, including uncommitted edits. |

Local git repositories must use explicit `file://` URIs; raw filesystem paths
are not supported. `file://` entries do not support `@branch-or-tag` selectors,
and uncommitted changes are ignored. Development directory sources must use
explicit `dir://` URIs, do not support refs, and are copied into Lyra-managed
catalog and runner directories on sync or refresh. Omit a trailing `.git` suffix
for GitHub entries. Make sure the API and worker containers can reach each
GitHub or `file://` repository with `git`.

For Docker Compose, `file://` and `dir://` paths must be reachable inside every
API and worker container at the same absolute path used in the repo source. A
development directory source usually needs a bind mount:

```yaml
volumes:
  - ./mock-plugin:/plugins/mock-plugin
```

Then register it with:

```json
{"source": "dir:///plugins/mock-plugin"}
```

Workers do not hot-reload plugin code in process. Refresh the catalog so the API
reloads manifests and worker pools restart and reinstall copied plugin snapshots.

## Preflight Checks

Run these checks from the plugin repository before publishing the branch or tag
that Lyra will use. They catch the most common packaging and manifest problems
before a worker has to diagnose them.

Check that the package is installable in the same style as the worker
compatibility check:

```bash
uv pip install --python "$(which python)" --dry-run .
```

Install editable, verify the registry imports, and check the generated artifact:

```bash
uv pip install --python "$(which python)" -e .
uv run python -c "from example_plugin.metrics import plugin; print(plugin.metric_names)"
uv run lyra-plugin check-manifest
```

Add direct tests for each decorated function using typed `GeoJSON`, scalar,
nested model, and batch arguments plus a small fake `RunContext`. Add at least
one adapter-level test that invokes the `PluginDefinition` with a resolved
`JobEnvelope`, proving the worker boundary parses the same types. Assert that
the returned result has the expected job ID, columns or media type, and table
index. For table metrics,
choose the `TableJobResult` constructor that matches the computation result:
`from_mapping()` for dictionaries or aligned sequences, `from_dataframe()` for
table-shaped Pandas or GeoPandas outputs, and `from_series()` for one-column
Pandas outputs. For batched table metrics, include at least two source items and
assert the expanded column names and order.

For a static square-metre column with a `fraction_of_location_area`
derivation, assert that the runner returns only the declared source column.
Lyra owns the EPSG:6372 location-area calculation and appends the fraction
after runner validation.

Do not edit the manifest. Change the Python signature or decorator metadata,
run `lyra-plugin build-manifest`, and commit both changes. Metric names must be
unique, every metric needs a spatial input, and table metrics need a
`location: LocationInput` parameter.

Workers import registries only for metrics whose server-assigned queue belongs
to the worker's `[workers.<name>].queues` list. If a selected entrypoint cannot
be imported, or its live contract differs from the manifest, the worker
registry will not load.

## Connect And Smoke Test

After pushing the plugin, configure the repository and run at least one worker
whose queue matches the metric's server assignment:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/example-lyra-plugin@main"}'
```

```bash
uv run python -m lyra_app.worker_launcher interactive
```

Refresh the API catalog, then restart workers when the response recommends it:

```bash
curl -X POST http://localhost:5219/admin/plugin-catalog/refresh \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"

curl -X POST 'http://localhost:5219/admin/workers/restart?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

If the metric needs a non-default queue, set it through
`/admin/plugin-routing/{metric_name}` and restart the matching worker pool.

Confirm the metric is listed:

```bash
curl http://localhost:5219/metrics/example_metric
```

Submit a minimal job that matches the effective schema returned by `/metrics`.
For the quickstart plugin, use the complete `example_metric` payload in
[Plugin Quickstart](../plugin-quickstart/). For your own plugin, copy the field
names and wrapper shape from `/metrics/{metric_name}` instead of reusing the
example payload without checking it.

## Common Failures

| Symptom | What to try |
| --- | --- |
| `GET /metrics` is empty | `[plugins].repos` lists reachable repositories and each repo has a root `lyra.plugin.json`. |
| Worker exits or restarts at startup | An installed manifest failed to parse, a selected metric name is duplicated, or a selected entrypoint failed to import. Run the preflight commands and check worker logs. |
| Metric appears in `/metrics`, but the job fails as `unknown_metric` | The worker skipped an incompatible plugin, editable install failed, or the worker config does not include the metric's assigned queue. Check worker logs. |
| Job remains `queued` | No worker is consuming the metric's assigned queue with matching Celery `-Q` settings. |
| `POST /jobs` returns `422` | Fetch `/metrics/{metric_name}` and match the effective schema. Spatial fields must use wrapper objects, not top-level raw GeoJSON. |
| Spatial resolution returns `503` | Database-backed wrappers such as `cvegeo_list` or `met_zone_code` could not be resolved. Check database settings and availability. |
| Worker returns `invalid_result` | Return `TableJobResult` or `FileJobResult` with the same `job_id` as the envelope and a payload matching the manifest `output`. |

For output design, see [Metric Output Design](../metric-output-design/). For
manifest details, see [Plugin Manifests](../plugin-manifests/). For runner
behavior, see [Runner Plugins](../runner-plugins/).
