# Implement Result Polling And Raw Access Tools

## Goal

Implement MCP tools that let agents continue running jobs, inspect descriptors,
preview results, and hand raw downloads to client runtimes without inlining
large tables into model context.

## Background from the discussion

Agents can handle asynchronous jobs when the tool contract includes the next
tool and stable result reference. Raw tables are needed for downstream
developer analysis, but normal MCP responses should remain compact.

## Scope

- Implement `lyra_get_job_result`.
- Implement `lyra_get_result_metadata`.
- Implement `lyra_get_result_preview`.
- Implement `lyra_download_result` as raw-access metadata or authenticated
  download handoff for JSONL.
- Ensure every tool accepts `lyra://results/{job_id}`.

## Out of scope

- Returning full raw tables inline by default.
- Signed URLs.
- CSV, Parquet, SQL, joins, and statistics.

## Files or areas likely affected

- `packages/lyra_mcp/src/lyra/mcp`
- `packages/lyra_api/src/lyra/api/client`
- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- `tests/test_mcp_server.py`
- `tests/test_api_client_jobs.py`
- `docs/src/content/docs/ai-agent-guide.md`

## Required behavior

- `lyra_get_job_result(result_ref, wait_seconds=30)` waits briefly and returns
  either `running` or a terminal descriptor/error envelope.
- `lyra_get_result_metadata` returns descriptor metadata without requiring raw
  table hydration.
- `lyra_get_result_preview` returns the descriptor preview rows and summary.
- `lyra_download_result` returns enough authenticated raw-access information for
  the client runtime to fetch JSONL through `lyra-api`.
- Expired results return a structured error that tells the agent the job must be
  rerun if the user still wants the data.

## Implementation notes

- Prefer calling `lyra-api` helper methods rather than duplicating HTTP code.
- Keep raw-access tool output compact and deterministic.
- Include `expires_in_seconds` whenever available so clients can decide whether
  to download immediately.
- Do not invent local file paths inside MCP responses unless the MCP runtime
  actually wrote a file.

## Tests and verification

- Test polling from running to succeeded.
- Test expired result refs.
- Test descriptor, preview, and download metadata tools.
- Test failed and cancelled job envelopes.

## Step exit checklist

- Result continuation tools are implemented and tested.
- Raw access avoids model-context table dumps.
- Running responses consistently identify the next tool to call.
- Expired results produce clear structured errors.

## Decision gate before the next step

Confirm that the MCP result tools plus `lyra-api` helpers support the planned
developer-side analysis flow.

## Next-step context

The next step will document the end-to-end agent and developer experience.
