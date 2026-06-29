---
title: Plugin Author Checklist
description: Validate, publish, connect, and troubleshoot a third-party Lyra plugin.
---

Use this checklist before you point `LYRA_PLUGIN_REPOS` at a third-party plugin
repository. For the minimal files and code, start with
[Plugin Quickstart](../plugin-quickstart/).

## Trust Model

Lyra plugin repositories are trusted code. API containers read only
`lyra.plugin.json` manifests, but worker containers clone plugin repositories,
run `uv pip install`, install compatible packages editable, import matching
entrypoints, and execute plugin code with the worker container's permissions.

Only configure repositories you are willing to run inside the worker
environment. Keep worker secrets, network access, mounted volumes, and service
accounts scoped to what plugin code is allowed to use.

## Repository Checklist

- Put `lyra.plugin.json` at the repository root.
- Make the repository an installable Python package with `pyproject.toml`.
- Depend on `lyra-sdk` for runner contracts.
- Add `lyra-utils` only when plugin code uses GeoDataFrame, date, or Earth
  Engine helpers.
- Keep runner imports under the installed package, such as
  `example_plugin.runner:run`.
- Do not import from `lyra_app` in plugin code.

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
| `https://github.com/owner/repo@branch-or-tag` | Same as above with an explicit GitHub URL prefix. |

Local filesystem paths are not supported. Omit a trailing `.git` suffix. The
repository must be reachable by `git` from the API and worker containers.

## Preflight Checks

Run these commands from the plugin repository before publishing the branch or
tag that Lyra will use.

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

The manifest must be strict v2 JSON. Extra fields are rejected, schemas must be
valid JSON Schemas, metric names must be unique across the loaded catalog, and
each spatial input must be a required object property.

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

Submit a minimal job that matches the effective schema returned by `/metrics`:

```bash
curl -X POST http://localhost:5219/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "metric": "example_metric",
    "input": {
      "location": {
        "data_type": "geojson",
        "value": {
          "type": "FeatureCollection",
          "features": [
            {
              "id": "area-1",
              "type": "Feature",
              "geometry": {
                "type": "Polygon",
                "coordinates": [[
                  [-99.20, 19.30],
                  [-99.10, 19.30],
                  [-99.10, 19.40],
                  [-99.20, 19.40],
                  [-99.20, 19.30]
                ]]
              },
              "properties": {}
            }
          ],
          "crs": {
            "type": "name",
            "properties": { "name": "EPSG:4326" }
          }
        }
      },
      "value": 1
    }
  }'
```

## Common Failures

| Symptom | Check |
| --- | --- |
| `GET /metrics` is empty | `LYRA_PLUGIN_REPOS` is set in the API environment, repos are reachable, and each repo has a root `lyra.plugin.json`. |
| Metric appears in `/metrics`, but the job fails as `unknown_metric` | Worker install/import failed, the worker skipped an incompatible plugin, or `LYRA_RUNNER_QUEUES` does not include the metric queue. Check worker logs. |
| Job remains `queued` | No worker is consuming the manifest queue with matching Celery `-Q` settings. |
| `POST /jobs` returns `422` | Fetch `/metrics/{metric_name}` and match the effective schema. Spatial fields must use wrapper objects, not top-level raw GeoJSON. |
| Spatial resolution returns `503` | Database-backed wrappers such as `cvegeo_list` or `met_zone_code` could not be resolved. Check database settings and availability. |
| Worker returns `invalid_result` | Return a `JobResult` with the same `job_id` as the envelope and a terminal status. |

For manifest details, see [Plugin Manifests](../plugin-manifests/). For runner
behavior, see [Runner Plugins](../runner-plugins/).
