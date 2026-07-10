# Deduplicate Idempotent Submissions

## Goal

Ensure network retries and concurrent duplicate requests dispatch at most one
expensive metric job.

## Background from the discussion

The current `idempotency_key` is merely passed to workers. If a client loses the
job-creation response, retrying can create duplicate Earth Engine work.

## Scope

- Create a shared job-submission service used by REST and in-process MCP calls.
- Canonically fingerprint the metric plus validated unresolved public request.
- Atomically bind an agent-scoped idempotency key to one request fingerprint and
  job ID in Redis.
- Return the existing job for equivalent replays and conflict for different
  requests.
- Expose idempotency through `lyra_run_metric` and both Python clients.
- Retain idempotency records for at least the job-store TTL.

## Out of scope

- Multiple agent principals or cross-principal ownership.
- Content-addressed deduplication without an explicit key.
- Rate limiting, caching successful metric results, or retrying worker failures.

## Files or areas likely affected

- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- `lyra_app/job_store.py`
- `lyra_app/job_submission.py`
- `lyra_app/routes/jobs.py`
- MCP input/output models and tools.
- Sync and async `lyra-api` clients.
- Job-store, route, client, and MCP tests.

## Required behavior

- The first accepted key/request pair creates and dispatches one job.
- Equivalent sequential and concurrent replays return the same job ID with
  `reused: true` and never dispatch another task.
- Reusing a key for a different metric or validated request returns REST `409`
  and a structured MCP `idempotency_conflict` error.
- A new response reports `reused: false`.
- Canonical fingerprints are deterministic across mapping order and processes.
- Pre-dispatch failures do not leave an unusable permanent reservation.
- Idempotency records expire no earlier than associated status and result data.
- REST and MCP share one implementation rather than wrapping one another's route
  functions.

## Implementation notes

- Use Redis atomic primitives or a checked-in atomic script through the existing
  Redis client abstraction; process-local locks are insufficient.
- Store only the request digest and job identity in the idempotency index; the
  full public request already belongs to provenance.
- Do not treat an idempotent replay as a new queued transition or event.

## Tests and verification

- Use the manifest-declared idempotency tests.
- Include deterministic digest, sequential replay, concurrent replay, conflict,
  TTL, failure cleanup, REST, MCP, and client behavior.

## Step exit checklist

- [ ] Equivalent retries dispatch exactly once.
- [ ] Conflicting key reuse fails deterministically.
- [ ] REST and MCP return an explicit reused marker.
- [ ] Atomicity, failure cleanup, and TTL behavior are covered.
- [ ] Submission logic no longer lives in a route handler.

## Decision gate before the next step

Proceed only when a concurrency test proves one task dispatch for simultaneous
equivalent requests.

## Next-step context

The next step rate-limits only genuinely new submissions through the shared
submission service.
