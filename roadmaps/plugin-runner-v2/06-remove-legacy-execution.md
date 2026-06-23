# Step 6 - Remove Legacy Execution Paths

## Goal

Delete old execution paths after the generic job protocol works end to end. This is a cleanup step, not a compatibility step.

## Key Changes

- Remove `/ws/{metric}`.
- Remove or replace `/download_result/{download_id}` with `/jobs/{job_id}/result`.
- Remove `/models` if Step 2 chose to delete it.
- Remove dynamic Pydantic model generation from function signatures.
- Remove explicit legacy plugin callable modes:
  - `calculate`
  - `calculate_prepare`
  - `calculate_for_items`
  - `calculate_aggregate`
- Remove old metric-specific Celery dispatch and task registration.
- Remove transitional tests that assert legacy endpoint or manifest behavior.
- Update API docs and examples to use the job API only.

## Tests

- Full test suite passes without legacy route imports.
- No references remain to legacy callable modes.
- No references remain to per-metric Celery task registration.
- OpenAPI/docs do not advertise removed endpoints.

## Done Criteria

- The codebase has one execution protocol: v2 job envelopes.
- The public API no longer exposes legacy metric WebSocket or download paths.
- Core worker code no longer contains legacy wrapper branches.
