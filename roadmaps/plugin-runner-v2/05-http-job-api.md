# Step 5 - HTTP Job API

## Goal

Replace metric-specific WebSockets with an HTTP job API that does not require polling. Use a long-lived HTTP event stream for progress and completion.

## Key Changes

- Add job endpoints:
  - `POST /jobs`
  - `GET /jobs/{job_id}`
  - `GET /jobs/{job_id}/events`
  - `GET /jobs/{job_id}/result`
- `POST /jobs` request:
  - `metric`
  - `input`
  - optional `idempotency_key`
- `POST /jobs` behavior:
  - validate metric exists
  - validate `input` against the metric request schema
  - create `JobEnvelope`
  - store queued status
  - dispatch `lyra.run_metric` to the manifest execution queue
  - return `202 Accepted` with job URLs
- `GET /jobs/{job_id}/events` should use SSE by default:
  - content type: `text/event-stream`
  - events come from Redis Streams
  - endpoint closes after terminal status unless the client disconnects first
- `GET /jobs/{job_id}/result` returns the final JSON result or file response.

## Tests

- `POST /jobs` dispatches `lyra.run_metric` to the manifest queue.
- Invalid metric returns 404.
- Invalid input returns validation error.
- `GET /jobs/{job_id}` returns current state.
- `GET /jobs/{job_id}/events` streams queued/progress/terminal events.
- `GET /jobs/{job_id}/result` returns 404 before completion and final payload after success.
- File results are served correctly.

## Done Criteria

- Clients can submit long-running jobs and wait via HTTP streaming, not polling.
- No client needs `/ws/{metric}` for non-polling progress.
- Job endpoints are documented as the primary public API.
