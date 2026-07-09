# Add Result Reference Helpers To lyra-api

## Goal

Make `lyra-api` the first developer-facing helper layer for result refs,
descriptors, raw downloads, and optional local DataFrame hydration.

## Background from the discussion

Developers building agent clients should not reimplement auth, result-ref
parsing, downloads, pagination decisions, or table decoding. A separate
analysis package can wait until helper scope grows.

## Scope

- Add sync and async client helpers for result descriptors.
- Add sync and async JSONL download helpers.
- Add result-ref parsing and validation.
- Add optional DataFrame hydration if pandas is installed, without making pandas
  a required runtime dependency.

## Out of scope

- A separate `lyra-analysis` package.
- Server-side analysis.
- Parquet support.

## Files or areas likely affected

- `packages/lyra_api/src/lyra/api/client/sync.py`
- `packages/lyra_api/src/lyra/api/client/async_.py`
- `packages/lyra_api/src/lyra/api/client/base.py`
- `packages/lyra_api/src/lyra/api/__init__.py`
- `tests/test_api_client_jobs.py`
- `docs/src/content/docs/lyra-api.md`
- `docs/src/content/docs/python-client.md`

## Required behavior

- `get_result_descriptor(result_ref_or_job_id)` fetches the descriptor.
- `download_result(result_ref_or_job_id, path, format="jsonl")` writes raw
  JSONL for table results.
- `result_dataframe(result_ref_or_job_id)` is optional and raises a clear client
  error if pandas is unavailable.
- Helpers accept both `lyra://results/{job_id}` and raw job ids.
- Errors remain `LyraAPIError`/`DownloadError` compatible.

## Implementation notes

- Keep sync and async method names parallel.
- Reuse existing `_http_url`, headers, timeout, and error conventions.
- Do not add pandas to runtime dependencies unless the project explicitly
  chooses to hard-depend on DataFrame hydration.
- Keep raw JSONL streaming behavior consistent with file downloads.

## Tests and verification

- Extend fake sync and async HTTP tests.
- Cover refs, raw job ids, JSONL download, descriptor parse, optional pandas
  missing behavior, and HTTP errors.

## Step exit checklist

- Sync and async clients expose equivalent helpers.
- Docs show a local correlation workflow that downloads raw results and computes
  statistics outside Lyra.
- Existing job helper behavior still passes tests.

## Decision gate before the next step

Confirm the client helper surface is stable enough for the MCP package to use
without reaching around `lyra-api`.

## Next-step context

The next step will scaffold the MCP server package and bearer-token protected
mount point.
