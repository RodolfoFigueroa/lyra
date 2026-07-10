# Rate-Limit Agent Job Submissions

## Goal

Protect expensive metric execution with a configurable Redis-backed submission
limit shared by REST and MCP.

## Background from the discussion

A single trusted agent credential is sufficient for this version, but without a
server-side quota a faulty or compromised client can enqueue unbounded work.
Idempotency must be established first so harmless retries do not consume quota.

## Scope

- Add strict agent submission-limit configuration with defaults of 10 new jobs
  per 60 seconds.
- Implement an atomic Redis fixed-window counter for the shared agent principal.
- Enforce it in the shared job-submission service.
- Return REST `429` with `Retry-After` and structured MCP retry metadata.
- Keep public catalog/lookup reads, polling, previews, and downloads outside the
  submission limit.

## Out of scope

- Per-user or per-metric quotas, concurrent-worker limits, billing, and OAuth.
- Rate-limiting ordinary metadata reads.
- Reverse-proxy denial-of-service controls outside the application.

## Files or areas likely affected

- `config.example.toml`
- `lyra_app/config.py`
- `lyra_app/job_store.py`
- `lyra_app/job_submission.py`
- `lyra_app/routes/jobs.py`
- MCP contract models and tools.
- Config, job-store, route, and MCP tests.

## Required behavior

- At most the configured number of new jobs is accepted in one configured
  fixed window across REST and MCP combined.
- Equivalent idempotent replays, validation failures, idempotency conflicts, and
  read-only calls do not consume capacity.
- A rejected request creates no status, provenance, idempotency, or Celery task.
- REST responses include `429` and an integer `Retry-After` value.
- MCP errors use code `rate_limited` and include `retry_after_seconds`.
- Counters expire automatically and do not outlive their window.
- Configuration validation rejects booleans, zero, and negative limits/windows.

## Implementation notes

- Keep the algorithm deterministic and dependency-light; use Redis atomic
  increment/expiry semantics rather than an in-memory limiter.
- Define ordering with idempotency explicitly: check an existing equivalent
  binding first, then consume quota only for a new reservation.
- Do not include secret token material in rate-limit keys; use a constant shared
  agent-principal identifier for this version.

## Tests and verification

- Use the manifest-declared rate-limit tests.
- Cover window boundaries, TTL, combined REST/MCP usage, replay exemption,
  conflict exemption, rejection side effects, retry metadata, and invalid config.

## Step exit checklist

- [ ] New job submissions are Redis-rate-limited.
- [ ] REST and MCP share one counter and error contract.
- [ ] Safe replays and reads do not consume quota.
- [ ] Rejected submissions have no persisted or queued side effects.
- [ ] Configuration and window behavior are deterministic and tested.

## Decision gate before the next step

Proceed only when combined REST/MCP tests prove the limit cannot be bypassed or
double-counted.

## Next-step context

The next step improves how agents resolve natural-language locations and find
metrics without adding semantic search infrastructure.
