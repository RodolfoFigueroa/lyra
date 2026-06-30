# 06 Lyra API Client Contract

## Goal

Expose the finalized route surface through `packages/lyra_api` so the future TUI
can use typed client methods instead of raw HTTP calls.

## Background From The Discussion

The TUI should use `lyra-api` as its boundary. It should not import
`lyra_app`, use raw `requests` everywhere, inspect Redis, or call Celery.

Earlier steps may update some client methods opportunistically. This step is the
explicit pass to make the client complete and coherent.

## Scope

- Add sync and async client methods for all new public and admin routes.
- Add shared request/response models to `lyra-sdk` where they are public
  contracts.
- Decide how admin bearer tokens are passed through the client.
- Update generated API docs if needed.
- Add client tests for success and important error cases.

## Out Of Scope

- Do not implement the TUI.
- Do not add a separate CLI.
- Do not expose low-level Redis/Celery details in the client.

## Files Or Areas Likely Affected

- `packages/lyra_api/src/lyra/api/client/base.py`
- `packages/lyra_api/src/lyra/api/client/sync.py`
- `packages/lyra_api/src/lyra/api/client/async_.py`
- `packages/lyra_api/src/lyra/api/__init__.py`
- `packages/lyra_sdk/src/lyra/sdk/models/`
- `packages/lyra_sdk/src/lyra/sdk/models/__init__.py`
- `tests/test_api_client_jobs.py` or new client test files
- `docs/src/content/docs/python-client.md`
- `docs/src/content/docs/lyra-api.md`
- `docs/scripts/generate_api_docs.py`
- `docs/src/content/docs/api-reference/`

## Required Behavior

- Existing job methods continue to work with the new route names:
  - `get_data_types()` calls `/data-types`.
  - `get_job_result()` returns JSON metadata/results.
  - `download_job_result_to_file()` calls `/result/download`.
- Add methods for:
  - health
  - met-zone lookup
  - plugin source list/create/update/delete/sync through the existing
    `/admin/plugin-repos` routes
  - plugin catalog refresh
  - plugin routing list/set/delete
  - worker restart
  - job listing
  - job cancellation
  - status
  - config summary
  - catalog summary
  - worker list/detail
  - queue list
- Error behavior should remain consistent with existing `DownloadError` or a
  renamed/generalized API error if chosen.
- Async and sync clients should have equivalent method names and return shapes.

## Implementation Notes

- Consider whether `DownloadError` is too job-result-specific for admin routes.
  If renaming or introducing `LyraAPIError`, update imports and docs carefully.
- Admin auth can continue using `headers={"Authorization": "Bearer ..."}` at
  construction, but a convenience constructor or `admin_api_key` parameter may be
  useful. If added, keep it explicit and avoid reading environment variables
  inside the library.
- Do not over-validate plugin source strings in the client. The server owns
  parsing and validation for GitHub entries, `file://` local git repositories,
  and `dir://` directory snapshots.
- Keep the base URL builder simple, but fix any path issues that produce trailing
  slash surprises.
- Prefer SDK Pydantic models over loose `dict[str, Any]` when response shape is
  known and stable.

## Tests And Verification

- Add tests for every new client method in sync and async forms.
- Test admin authorization header propagation.
- Test plugin source create/update methods with a `dir://` source string.
- Test non-2xx responses produce useful exceptions.
- Test file downloads use `/result/download`.
- Run:

  ```bash
  uv run pytest tests/test_api_client_jobs.py
  uv run ruff format packages/lyra_api packages/lyra_sdk tests
  uv run ruff check packages/lyra_api packages/lyra_sdk tests
  uv run ty check
  ```

## Completion Criteria

- The future TUI can perform every agreed route operation through `lyra-api`.
- Sync and async clients have matching capabilities.
- Client docs mention the admin/operator methods.
- Client tests cover route paths and response parsing.

## Handoff Notes For The Next Step

After this step, run the final validation plan in `99-validation.md`. Do not
begin TUI implementation until final validation is green or remaining failures
are explicitly accepted.
