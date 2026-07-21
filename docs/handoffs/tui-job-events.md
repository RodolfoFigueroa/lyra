# TUI job-event integration handoff

## Purpose and current boundary

The event infrastructure is complete, but the TUI intentionally only received
the lifecycle rename from `started` to `running`. This document defines a future
implementation that makes progress and messages visible without changing the
server contracts again.

The authoritative contracts live in `lyra.sdk.models.job`:

- Lifecycle states are `queued`, `running`, `succeeded`, `failed`, and
  `cancelled`.
- `JobEventRecord.id` is the resumable Redis/SSE cursor.
- `JobEventRecord.event` is a discriminated union on `kind`: `lifecycle`,
  `progress`, or `message`.
- `JobStatusInfo.progress` and `latest_message` are compact recovery projections.
- The synchronous API client exposes `JobHandle`; the async client exposes
  `AsyncJobHandle`. Both support status, resumable events, waiting, and results.

Do not parse raw SSE in the TUI. Use `AsyncJobHandle.events()` or
`AsyncLyraAPIClient.iter_job_events()` so authentication, validation,
reconnection, deduplication, cursor gaps, and deadlines have one implementation.

## Recommended experience

Add an event-aware job detail view while keeping the existing job list compact.
The list should show lifecycle state plus a concise progress projection, such as
`RUN 42/100 tiles`, when available. Selecting a job should open a detail panel
with:

1. lifecycle, metric, timestamps, and result state;
2. the current stage, numeric progress, unit, and optional progress message;
3. a bounded chronological message feed with severity styling;
4. terminal error details and result actions.

Progress is replaceable state; messages are history. Do not append every
progress event to a visible log. Update the progress widget in place. Retain a
bounded message deque in memory (a few hundred entries is sufficient) and let
the server remain the durable source.

## Controller and state design

Introduce a per-selected-job observation controller owned by the screen or app
state layer. It should hold the task, job ID, last processed event ID, current
status, progress projection, message deque, and connection state. Starting a
new observation must cancel and await the prior task before replacing state.

On selection:

1. fetch `JobStatusInfo` and render its progress/message projections immediately;
2. start `AsyncJobHandle.events(after_id=last_event_id)`;
3. store `record.id` only after the UI state update succeeds;
4. replace progress on `JobProgressEvent`;
5. append and bound messages on `JobMessageEvent`;
6. update lifecycle and stop after a terminal `JobLifecycleEvent`;
7. fetch the terminal result or descriptor only when the UI needs it.

The client already suppresses duplicate cursor IDs. The TUI should nevertheless
make reducers idempotent because widget refreshes and screen remounts can replay
the current status projection.

## Failure behavior

- `JobWaitTimeoutError`: normally avoid a finite deadline for an actively viewed
  job; if one is used, show a nonterminal disconnected state.
- `JobEventStreamError`: keep the last projection, show a reconnect action, and
  allow polling status without discarding the cursor.
- `JobEventCursorGapError`: fetch current status, clear historical messages,
  explain that older messages expired, reset the cursor to the latest known
  point, and begin a fresh stream. Never imply that the projection is a complete
  historical feed.
- `404`: show the job as expired and stop observation.

## Implementation sequence

1. Add pure reducers for `JobStatusInfo` and each typed event.
2. Add controller tests with a fake async event iterator, including selection
   changes and task cancellation.
3. Add progress and message widgets to the detail screen.
4. Wire result actions only after terminal lifecycle events.
5. Add Textual integration tests for progress replacement, severity styling,
   reconnect state, cursor gaps, terminal shutdown, and screen unmount cleanup.

Keep networking out of widgets and never mutate Textual state from an unmanaged
background coroutine. Use the app's worker/task mechanism and marshal updates
through the normal UI event loop.

## Acceptance criteria

- Viewing a running job shows its status projection before the SSE connection
  produces a record.
- Progress updates in place and cannot visually regress within a stage.
- Messages are ordered, severity-styled, bounded, and do not include diagnostic
  application log records.
- Reopening or reconnecting resumes from the last applied `JobEventRecord.id`.
- A cursor gap is explicit and recovers from `JobStatusInfo`.
- Observation stops cleanly on terminal state, selection change, and unmount.

