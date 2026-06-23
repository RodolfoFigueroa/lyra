# Step 1 - Define The New Contract

## Goal

Introduce the breaking v2 execution contract without changing runtime behavior yet. This step establishes the shared language for the API, workers, and plugin services.

## Key Changes

- Add SDK models for the new job protocol:
  - `JobEnvelope`: `job_id`, `metric`, `input`, optional `idempotency_key`, optional metadata.
  - `JobEvent`: `job_id`, `event`, `timestamp`, `data`.
  - `JobResult`: `job_id`, `status`, optional `result`, optional `result_type`, optional `file_path`, optional `error`.
- Replace the transitional manifest shape with a simplified v2 `PluginManifest`:
  - `schema_version`
  - `plugin`
  - `metrics`
  - per metric: `name`, `description`, `request_schema`, optional `result_schema`, `execution`, `entrypoint`
- Define one plugin entrypoint contract:
  - `entrypoint`: `module:function`
  - callable signature: `run(job: JobEnvelope, context: RunContext) -> JobResult`
- Define `RunContext` as the worker-provided object for services such as DB access, logging, event emission, temp paths, and cancellation checks.

## Tests

- Validate accepted and rejected manifest examples.
- Validate accepted and rejected job envelopes, events, and results.
- Validate entrypoint strings must be `module:function`.
- Validate JSON Schema fields are structurally valid when present.

## Done Criteria

- SDK exposes the v2 contract models.
- No API route, worker, or deployment behavior changes yet.
- Tests document the intended breaking contract.
