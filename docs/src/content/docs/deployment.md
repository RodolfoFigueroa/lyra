---
title: Deployment
description: Run the API as a manifest-only service and workers as warm queue-specific runners.
---

Lyra v2 separates API catalog loading from runner execution.

## API Containers

API containers:

- Read static plugin manifests from `/lyra_plugin_catalog`.
- Validate job requests using metric `request_schema` values.
- Dispatch the generic `lyra.run_metric` Celery task to the metric's manifest queue.
- Do not install or import runner plugin code.

This keeps API startup and request validation independent from plugin runtime dependencies.

## Worker Containers

Worker containers:

- Clone and install plugin repositories at startup through the worker startup path.
- Read v2 manifests from installed plugins.
- Filter metrics by `LYRA_RUNNER_QUEUES`.
- Import matching metric entrypoints.
- Consume matching Celery queues with `-Q`.

Example:

```bash
LYRA_RUNNER_QUEUES=interactive \
celery -A lyra_app.worker.celery_app worker --loglevel=info -Q interactive
```

## Docker Compose

The Compose examples define two generic worker pools:

- `interactive`
- `batch`

Both use the same Lyra image and generic Celery task code. Each worker pool has its own `/lyra_plugins` volume, while the API mounts only `/lyra_plugin_catalog`.

To add another queue, add another worker service using the same image, set `LYRA_RUNNER_QUEUES` to the new queue name, and set Celery `-Q` to the same queue.

## Plugin Updates

Plugin updates are explicit:

1. Refresh the API manifest catalog with `POST /update-plugins`.
2. Restart warm worker pools so they reinstall plugin code and rebuild their runner registries.

Workers do not hot-reload plugin code in-process.

Kubernetes manifests are not part of the checked-in deployment shape.
