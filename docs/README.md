# API Documentation

## REST API

Interactive documentation is available when the server is running:

- **Swagger UI** (interactive): http://localhost:5219/docs
- **ReDoc** (clean HTML): http://localhost:5219/redoc

These are automatically generated from the FastAPI application and include:
- All REST endpoints (`/data_types`, `/metrics`, `/metrics/{metric_name}`, `/jobs`, `/jobs/{job_id}`, `/jobs/{job_id}/events`, `/jobs/{job_id}/result`, `/met_zone_code`)
- Request/response schemas
- Parameter descriptions

## Deployment Shape

Lyra uses a v2 warm-worker model:

- API containers read plugin manifests, validate requests, and dispatch
  `lyra.run_metric`; they do not install or import plugin code.
- Worker containers install plugin repos at startup and consume only the Celery
  queues listed in `LYRA_RUNNER_QUEUES`.
- Queue names are deployment-owned. Each metric chooses its queue through the
  v2 manifest `execution.queue` field.
- To add a queue, add a worker service with matching `LYRA_RUNNER_QUEUES` and
  Celery `-Q` values.
- Plugin updates refresh the API manifest catalog and restart worker pools; no
  in-process worker hot reload is attempted.

The checked-in deployment example is Docker Compose only. Kubernetes manifests
are not part of this step.
