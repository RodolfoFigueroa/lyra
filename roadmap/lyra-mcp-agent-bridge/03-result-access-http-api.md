# Add Public Result Descriptor And JSONL Export Routes

## Goal

Expose result descriptors and raw JSONL table exports through Lyra's public HTTP
API so MCP tools and ordinary clients use the same data plane.

## Background from the discussion

Agents should receive a compact descriptor, not thousands of table rows. Agent
developers still need raw data for local analysis, so Lyra must provide a
machine-consumable raw export path.

## Scope

- Add a public descriptor route for completed or running job refs.
- Add JSONL export for successful table results.
- Keep existing `/jobs/{job_id}/result` behavior intact.
- Return structured errors for expired, missing, failed, cancelled, and wrong
  result-kind cases.

## Out of scope

- CSV or Parquet export.
- Signed URLs.
- Server-side filtering, SQL, joins, or statistics.

## Files or areas likely affected

- `lyra_app/routes/jobs.py`
- `lyra_app/job_store.py`
- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- `tests/test_jobs_route.py`
- `docs/src/content/docs/job-api.md`
- `docs/src/content/docs/reference.md`

## Required behavior

- A descriptor endpoint returns the descriptor envelope for successful table and
  file results, and structured status/error envelopes for non-success terminal
  states.
- The descriptor includes raw access metadata advertising JSONL.
- JSONL export streams one row per line for successful table results.
- JSONL rows include the result index field and all table columns.
- Missing or expired results return the existing 404 style.
- File results continue to use the existing download endpoint.

## Implementation notes

- Prefer adding routes under `/jobs/{job_id}/result/...` so the lifecycle stays
  attached to existing job resources.
- Use existing Redis availability checks and `parse_job_result`.
- Avoid route names that imply SQL or arbitrary query behavior.
- Keep memory use bounded when streaming JSONL.

## Tests and verification

- Add route tests for descriptor, table JSONL export, file descriptors, failed
  descriptors, cancelled descriptors, and expired results.
- Keep existing result and download route tests passing.

## Step exit checklist

- Public descriptor route is tested.
- Public JSONL export route is tested.
- Docs list the new routes and explain result refs.
- Existing job API behavior is not regressed.

## Decision gate before the next step

Confirm the HTTP data plane is sufficient for `lyra-api` helper methods without
MCP-specific shortcuts.

## Next-step context

The next step will wrap these routes in sync and async client helpers.
