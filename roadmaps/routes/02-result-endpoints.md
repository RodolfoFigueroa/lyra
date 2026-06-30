# 02 Result Endpoints

## Goal

Separate job result metadata/JSON retrieval from file download behavior.

## Background From The Discussion

The current `GET /jobs/{job_id}/result` endpoint returns terminal JSON results
or serves file bytes for `FileJobResult`. When it serves a file, it schedules
cleanup of the file and deletes the stored result payload afterward. That is
surprising for an operator TUI because repeated inspection or download should be
predictable.

## Scope

- Keep `GET /jobs/{job_id}/result` for terminal JSON results and stable file
  result metadata.
- Add `GET /jobs/{job_id}/result/download` for file bytes.
- Remove the "generic result fetch deletes file result" behavior.
- Update SDK/client behavior and tests for file results.
- Update docs for table, failed, cancelled, and file result flows.

## Out Of Scope

- Do not add job listing or cancellation.
- Do not design a full artifact retention or explicit cleanup API unless needed
  for tests.
- Do not change runner file-result creation semantics.

## Files Or Areas Likely Affected

- `lyra_app/routes/jobs.py`
- `lyra_app/job_store.py` only if helper behavior is needed
- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- `packages/lyra_api/src/lyra/api/client/sync.py`
- `packages/lyra_api/src/lyra/api/client/async_.py`
- `tests/test_jobs_route.py`
- `tests/test_api_client_jobs.py`
- `docs/src/content/docs/job-api.md`
- `docs/src/content/docs/operations.md`
- `docs/src/content/docs/python-client.md`
- `docs/src/content/docs/lyra-api.md`
- `docs/public/llms.txt`

## Required Behavior

- For table, failed, and cancelled terminal results:
  - `GET /jobs/{job_id}/result` returns JSON as it does today.
- For file terminal results:
  - `GET /jobs/{job_id}/result` returns JSON metadata, including at minimum the
    existing `FileJobResult` fields.
  - `GET /jobs/{job_id}/result/download` streams the produced file bytes.
  - Downloading the file does not delete the stored result metadata.
  - If the file is missing, `/download` returns `404`.
- Client methods should make the split clear:
  - `get_job_result(job_id)` returns JSON terminal result metadata.
  - `download_job_result_to_file(job_id, path)` calls `/result/download`.

## Implementation Notes

- In `lyra_app/routes/jobs.py`, avoid returning `FileResponse` from
  `get_job_result`; reserve `FileResponse` for a new download handler.
- Consider using the existing `FileJobResult` model for `/result` metadata rather
  than inventing a second model.
- Keep `parse_job_result()` behavior aligned with `/result` JSON responses.
- If storage cleanup remains desirable, leave it for a later explicit admin route
  or retention policy. Do not hide cleanup inside a read endpoint.

## Tests And Verification

- Add or update route tests covering:
  - table JSON result through `/result`
  - failed/cancelled JSON result through `/result`
  - file metadata through `/result`
  - file bytes through `/result/download`
  - repeated `/result` calls for file metadata
  - repeated `/result/download` calls while the file exists
  - `404` when file metadata exists but file path is missing

- Run:

  ```bash
  uv run pytest tests/test_jobs_route.py tests/test_api_client_jobs.py
  uv run ruff format <touched-files>
  uv run ruff check <touched-files>
  ```

## Completion Criteria

- `/jobs/{job_id}/result` is stable and non-destructive.
- `/jobs/{job_id}/result/download` is the only route that streams file bytes.
- Sync and async clients call the correct route for file downloads.
- Docs describe the split clearly.

## Handoff Notes For The Next Step

The next step separates plugin catalog refresh from worker restart. It should
not rely on result endpoint internals.
