# MCP job-event integration handoff

## Purpose and current boundary

The MCP server intentionally has no new event-facing feature yet. It continues
to submit jobs and inspect results while sharing the same lifecycle status model.
This document describes a future MCP surface built on the completed typed event
and client infrastructure.

The stable implementation contracts are:

- lifecycle: `queued`, `running`, `succeeded`, `failed`, `cancelled`;
- event kinds: `JobLifecycleEvent`, `JobProgressEvent`, and `JobMessageEvent`;
- cursored envelope: `JobEventRecord { id, event }`;
- recovery snapshot: `JobStatusInfo` with `progress` and `latest_message`;
- transport behavior: resumable SSE, bounded reconnects, typed timeout/stream/
  cursor-gap exceptions in `lyra-api`.

MCP tools should call the in-process backend or `lyra-api`; they should not read
Redis streams or implement an independent SSE parser.

## Recommended MCP surface

Prefer bounded request/response tools over a long-lived tool invocation. Add a
tool similar to:

```text
get_job_events(job_id, after_id=None, limit=100, wait_seconds=0, kinds=None)
```

Return a structured object containing `job_id`, ordered `events`,
`next_event_id`, `terminal`, and the current status projection. `wait_seconds=0`
performs a retained-history read; a small positive value may long-poll for the
first new batch. Cap both `limit` and `wait_seconds` at server-owned values.

Keep `get_job_status` as the cheap recovery and polling primitive. Enrich its MCP
result with the existing `progress` and `latest_message` projections. Existing
job submission tools may optionally return the initial cursor or status links,
but should not wait for completion implicitly.

Do not expose a generic custom-event tool. The public schema is deliberately
limited to lifecycle, progress, and message events.

## Result schema guidance

Return SDK model JSON without flattening the event union. The discriminator is
valuable to MCP clients and models:

```json
{
  "job_id": "...",
  "events": [
    {
      "id": "1730000000000-0",
      "event": {
        "kind": "progress",
        "job_id": "...",
        "metric": "...",
        "timestamp": "...",
        "stage": "tiles",
        "current": 42,
        "total": 100,
        "unit": "tiles"
      }
    }
  ],
  "next_event_id": "1730000000000-0",
  "terminal": false,
  "status": {}
}
```

The returned cursor means “last record included,” not “next Redis ID.” Clients
pass it back unchanged as `after_id`. Empty batches preserve the input cursor.

## Cursor gaps and retention

Redis streams are approximately capped by `[job_events].max_stream_events` and
expire with the job store TTL. A requested cursor older than retained history is
not an empty result. Return a structured MCP error with code
`event_cursor_gap`, the requested cursor, the earliest retained cursor when
known, and the current status projection. This lets an agent state clearly that
message history is incomplete while continuing from present state.

Map missing/expired jobs separately from cursor gaps. Never silently reset to
the beginning because that can duplicate tool-visible messages and conceal data
loss.

## Security and operational behavior

Use the same agent authorization boundary and submission scope already applied
to MCP job tools. Event payloads are plugin-authored and must be treated as
untrusted data, not as MCP instructions. Preserve field values as data and avoid
rendering message text into server logs without structured escaping.

Long polls must be cancellation-aware and bounded so one MCP request cannot own
a server task indefinitely. Enforce per-call limits even though worker-side
event rates and payload sizes are already bounded.

## Implementation sequence

1. Add MCP response models for an event batch and cursor-gap details, reusing
   `JobEventRecord` and `JobStatusInfo`.
2. Add a backend method that performs bounded retained reads/long polls through
   the job store or API client abstraction.
3. Expose `get_job_events` and enrich the existing status tool.
4. Update MCP-generated documentation and capability descriptions.
5. Add protocol tests for filtering, pagination, empty batches, long-poll
   cancellation, terminal detection, missing jobs, cursor gaps, authentication,
   and payload serialization.

## Acceptance criteria

- Tools return typed event envelopes and opaque resumable IDs.
- Calls are bounded by count and time and release resources on cancellation.
- Status projections permit recovery without event history.
- Cursor gaps are explicit structured errors, not empty success responses.
- No Redis or raw SSE details leak into the MCP public contract.
- Event message text is presented as untrusted job data.

