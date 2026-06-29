---
title: Plugin Author Checklist
description: Validate, publish, connect, and troubleshoot a third-party Lyra plugin.
---

When your plugin works locally, use this checklist before you point
`LYRA_PLUGIN_REPOS` at its repository. If you are starting from an empty repo,
begin with [Plugin Quickstart](../plugin-quickstart/) and come back here before
publishing.

## Trust Model

Treat Lyra plugin repositories as trusted code. API containers read only
`lyra.plugin.json` manifests, but worker containers clone plugin repositories,
run `uv pip install`, install compatible packages editable, import matching
entrypoints, and execute plugin code with the worker container's permissions.

Configure only repositories you are willing to run inside the worker
environment. Keep worker secrets, network access, mounted volumes, and service
accounts scoped to what plugin code is allowed to use.

## Repository Checklist

- Put `lyra.plugin.json` at the repository root.
- Make the repository an installable Python package with `pyproject.toml`.
- Depend on `lyra-sdk` for runner contracts.
- Add `lyra-utils` only when plugin code uses GeoDataFrame, date, or Earth
  Engine helpers.
- Declare `geopandas`, `pandas`, or other libraries directly when plugin code
  imports them directly, even if another Lyra helper package also depends on
  them.
- Keep runner entrypoints under the installed package, such as
  `example_plugin.runner:run`.
- Import runtime contracts from `lyra-sdk` rather than from `lyra_app`.

## Repository Entries

`LYRA_PLUGIN_REPOS` is a comma-separated list of GitHub entries:

```text
LYRA_PLUGIN_REPOS=owner/plugin-a,owner/plugin-b@main,https://github.com/owner/plugin-c@v0.1.0
```

Supported forms are:

| Form | Meaning |
| --- | --- |
| `owner/repo` | Clone the repository's default branch. |
| `owner/repo@branch-or-tag` | Clone the named branch or tag. |
| `https://github.com/owner/repo` | Clone the repository's default branch with an explicit GitHub URL prefix. |
| `https://github.com/owner/repo@branch-or-tag` | Clone the named branch or tag with an explicit GitHub URL prefix. |

`LYRA_PLUGIN_REPOS` does not support local filesystem paths. Omit a trailing
`.git` suffix. Make sure the API and worker containers can reach the repository
with `git`.

## Preflight Checks

Run these checks from the plugin repository before publishing the branch or tag
that Lyra will use. They catch the most common packaging and manifest problems
before a worker has to diagnose them.

Check that the package is installable in the same style as the worker
compatibility check:

```bash
uv pip install --python "$(which python)" --dry-run .
```

Install editable and verify the entrypoint imports:

```bash
uv pip install --python "$(which python)" -e .
uv run python -c "from example_plugin.runner import run; print(run)"
```

Parse the manifest with the public SDK model:

```bash
uv run python -c "import json; from pathlib import Path; from lyra.sdk.models import PluginManifestV2; PluginManifestV2.model_validate(json.loads(Path('lyra.plugin.json').read_text())); print('manifest ok')"
```

Add at least one local runner test before publishing. Construct a resolved
`JobEnvelope`, pass a small fake `RunContext`, call the entrypoint directly, and
assert that the returned `TableJobResult` or `FileJobResult` has the expected
`job_id`, columns or media type, and serialized table index. For table metrics,
choose the `TableJobResult` constructor that matches the computation result:
`from_mapping()` for dictionaries or aligned sequences, `from_dataframe()` for
table-shaped Pandas or GeoPandas outputs, and `from_series()` for one-column
Pandas outputs.

The manifest is strict v2 JSON. Extra fields are rejected, schemas must be valid
JSON Schemas, metric names must be unique across the loaded catalog, and each
spatial input must be a required object property.

Workers import entrypoints only for selected queues. If `LYRA_RUNNER_QUEUES` is
unset, the worker selects every installed plugin metric. If a selected
entrypoint cannot be imported after editable install, the worker registry will
not load for that worker process.

## Connect And Smoke Test

After pushing the plugin, configure the repository and run at least one worker
whose queue matches the metric manifest:

```text
LYRA_PLUGIN_REPOS=owner/example-lyra-plugin@main
```

```bash
LYRA_RUNNER_QUEUES=interactive \
uv run celery -A lyra_app.worker.celery_app worker --loglevel=info -Q interactive
```

Refresh the API catalog and restart workers:

```bash
curl -X POST 'http://localhost:5219/update-plugins?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

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
| `GET /metrics` is empty | `LYRA_PLUGIN_REPOS` is set in the API environment, repos are reachable, and each repo has a root `lyra.plugin.json`. |
| Worker exits or restarts at startup | An installed manifest failed to parse, a selected metric name is duplicated, or a selected entrypoint failed to import. Run the preflight commands and check worker logs. |
| Metric appears in `/metrics`, but the job fails as `unknown_metric` | The worker skipped an incompatible plugin, editable install failed, or `LYRA_RUNNER_QUEUES` does not include the metric queue. Check worker logs. |
| Job remains `queued` | No worker is consuming the manifest queue with matching Celery `-Q` settings. |
| `POST /jobs` returns `422` | Fetch `/metrics/{metric_name}` and match the effective schema. Spatial fields must use wrapper objects, not top-level raw GeoJSON. |
| Spatial resolution returns `503` | Database-backed wrappers such as `cvegeo_list` or `met_zone_code` could not be resolved. Check database settings and availability. |
| Worker returns `invalid_result` | Return `TableJobResult` or `FileJobResult` with the same `job_id` as the envelope and a payload matching the manifest `output`. |

For manifest details, see [Plugin Manifests](../plugin-manifests/). For runner
behavior, see [Runner Plugins](../runner-plugins/).
