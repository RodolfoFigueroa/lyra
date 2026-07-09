# Define Result Descriptor Models And Summary Builders

## Goal

Add SDK-level models and server helpers for the JSON result descriptor that
agents and client libraries will consume instead of raw terminal result payloads.

## Background from the discussion

Lyra should always return a summary descriptor plus preview and raw-access
handles. Full raw data should be retrieved separately, and result references
should use the existing Redis job result lifetime.

## Scope

- Add result reference and descriptor models to `lyra-sdk`.
- Add table preview and numeric summary builders.
- Add Redis TTL helper support for `expires_in_seconds` and `expires_at`.
- Keep current `TableJobResult`, `FileJobResult`, failed, and cancelled result
  models as worker terminal results.

## Out of scope

- Parquet and CSV export.
- SQL, joins, correlation, regression, or statistical tools.
- Durable result promotion beyond Redis TTL.

## Files or areas likely affected

- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- `packages/lyra_sdk/src/lyra/sdk/models/__init__.py`
- `lyra_app/job_store.py`
- `tests/test_job_store.py`
- `tests/test_runner.py`
- `docs/src/content/docs/job-api.md`

## Required behavior

- `lyra://results/{job_id}` is the v1 result ref format.
- Descriptor models include status, `job_id`, `result_ref`, lifetime,
  table metadata, preview, summary, and raw access fields.
- Table previews are row-oriented JSON objects that include the result index
  under a named index field.
- Numeric summaries include at least `count`, `null_count`, `min`, `max`, and
  `mean`.
- Non-numeric columns may expose basic counts or omit numeric statistics.
- Descriptor building never changes stored terminal result payloads.

## Implementation notes

- Keep summary logic deterministic and dependency-light.
- JSON non-finite values should remain compatible with the existing job-store
  JSON behavior.
- Use Redis TTL or PTTL on `job:{job_id}:result` for lifetime values.
- If exact expiry cannot be computed, expose `expires_in_seconds` and omit
  `expires_at` rather than guessing.

## Tests and verification

- Unit-test result refs, descriptor validation, preview construction, summary
  statistics, and TTL behavior.
- Cover failed and cancelled results so descriptor tools can report terminal
  errors cleanly.
- Ensure existing terminal result parsing remains unchanged.

## Step exit checklist

- SDK exports descriptor models.
- Server helper code can build descriptors from stored terminal results.
- Redis lifetime fields are covered by tests.
- Existing job result behavior remains compatible.

## Decision gate before the next step

Confirm the descriptor model is expressive enough for HTTP routes and MCP tools
without adding server-side analysis features.

## Next-step context

The next step will expose descriptor and JSONL raw export routes over HTTP.
