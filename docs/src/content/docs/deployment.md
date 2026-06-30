---
title: Deployment
description: Run the API and workers from one server-owned TOML config and data volume.
---

Lyra separates API catalog loading from runner execution, but both roles read
the same `/lyra_data/config/lyra.toml` file and mount the same durable
`lyra_data` volume.

## API Containers

API containers:

- Read `/lyra_data/config/lyra.toml`.
- Create non-secret runtime directories under `/lyra_data`.
- Sync plugin manifests into `plugins.catalog_dir`.
- Validate job requests using compiled metric `request_schema` values.
- Assign missing metric queues in `[plugins.metric_queues]` using
  `plugins.default_queue`.
- Dispatch the generic `lyra.run_metric` Celery task to the metric's
  server-assigned queue.

The API catalog does not import plugin Python code.

## Worker Containers

Worker containers start with a worker name:

```bash
python -m lyra_app.worker_launcher interactive
```

The launcher reads `[workers.interactive]` for queue membership, concurrency,
install directory, and temp directory. It then starts Celery with the matching
`-Q` and concurrency values.

Workers:

- Clone and install plugin repositories at startup.
- Read schema v3 manifests from installed plugins.
- Import only metrics whose server-assigned queue belongs to the worker.
- Consume the same queues through Celery.

## Docker Compose

The Compose examples define one named volume:

```yaml
volumes:
  lyra_data:
    name: lyra_data
```

Every Lyra app container mounts it at `/lyra_data`. There are no separate plugin
catalog, worker plugin, cache, service-account, or env-file mounts.

The checked-in examples include two worker pools:

- `interactive`
- `batch`

To add another worker pool, add a `[workers.<name>]` table in TOML and another
service that runs `python -m lyra_app.worker_launcher <name>`.

## Filesystem Layout

Use this tree inside the volume:

```text
/lyra_data/
  config/lyra.toml
  cache/jobs/
  plugins/catalog/
  plugins/runners/
  secrets/
  logs/
```

Secret files are deployment-owned. Lyra references them by path and does not
generate placeholder secrets.

## Plugin Updates

Plugin updates are explicit:

1. Refresh the API manifest catalog with `POST /update-plugins`.
2. Let the API persist any new metric queue assignments to `lyra.toml`.
3. Restart warm worker pools so they reinstall plugin code and rebuild their runner registries.

Workers do not hot-reload plugin code in-process.

Kubernetes manifests are not part of the checked-in deployment shape.
