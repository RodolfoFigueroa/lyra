# Step 4 - Redis Job And Event Store

## Goal

Introduce a small Redis-backed job state layer that supports final results and non-polling event streams.

## Key Changes

- Store current job state under stable keys:
  - `job:{job_id}:status`
  - `job:{job_id}:result`
  - `job:{job_id}:events`
- Preferred event backend: Redis Streams.
  - Streams allow consumers to reconnect and resume from an event ID.
  - Keep a TTL on job keys and event streams.
- Job statuses:
  - `queued`
  - `started`
  - `progress`
  - `succeeded`
  - `failed`
  - `cancelled`
- `RunContext.emit_event(...)` writes progress events to the job event stream.
- Generic worker task writes status transitions and final results through this store.

## Tests

- Creating a job writes initial queued status.
- Worker status transitions update Redis.
- Progress events append in order.
- Final result is persisted with TTL.
- Failed jobs persist structured errors.
- Event reads can start from the beginning or after a known stream ID.

## Done Criteria

- API and worker use a shared job store abstraction.
- Job result retrieval no longer depends on the old `result_data_{task_id}` key format.
- Event storage is durable enough for client reconnects within the TTL.
