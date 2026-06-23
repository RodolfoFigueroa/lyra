# Step 7 - Deployment Shape

## Goal

Finalize the warm-only deployment model after the v2 job API and generic worker task are stable.

## Key Changes

- Keep warm worker pools only for v1.
- Compose/Kubernetes deployment defines operational queues explicitly.
- Each worker pool:
  - runs the same generic worker task
  - has `LYRA_RUNNER_QUEUES` set to one or more queues
  - installs plugin repos at startup
  - consumes matching Celery queues
- API container:
  - reads static manifests
  - validates requests
  - dispatches `lyra.run_metric`
  - never imports or installs plugin code
- Plugin update process:
  - refresh API manifest catalog
  - restart warm worker pools
  - do not attempt in-process worker hot reload
- Keep queue names arbitrary and deployment-owned, not limited to `lightweight`, `earth_engine`, or `heavy`.

## Tests

- Compose config validates.
- At least two warm queues can run from the same image with different `LYRA_RUNNER_QUEUES`.
- API dispatches to arbitrary queue names from manifests.
- Worker pool ignores metrics outside its configured queues.

## Done Criteria

- Deployment matches the v2 architecture:
  - manifest-only API
  - generic Celery task
  - warm queue-specific runners
- Operational docs describe how to add a queue, assign metrics, and restart workers after plugin updates.
