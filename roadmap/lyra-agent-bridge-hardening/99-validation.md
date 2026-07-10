# Final Validation

## Goal

Verify that Lyra exposes a standards-compliant, authenticated, reproducible, and
resource-controlled bridge for external metric-analysis agents.

## Implementation step checklist

- `01-official-mcp-transport.md` completed.
- `02-agent-auth-boundary.md` completed.
- `03-typed-mcp-tools.md` completed.
- `04-job-run-provenance.md` completed.
- `05-result-descriptor-provenance.md` completed.
- `06-idempotent-job-submission.md` completed.
- `07-agent-rate-limits.md` completed.
- `08-agent-discovery-utilities.md` completed.
- `09-authenticated-download-handoff.md` completed.
- `10-documentation-and-operations.md` completed.

## Repository-wide validation commands

Use the manifest-declared full Pytest, Ruff check, Ruff format, and Ty validation
descriptors. Build the Astro documentation through its existing package script.

## End-to-end scenarios

- Connect with the official Python MCP client, initialize, list tools, resolve a
  metropolitan-zone name, search and inspect two metrics, and close cleanly.
- Reject an invalid Origin, unsupported protocol version, malformed tool input,
  non-finite wait, missing agent token, invalid agent token, and admin token on
  an agent route.
- Confirm health, metrics, data types, and metropolitan-zone lookup remain
  public while every job lifecycle and result route requires the agent token.
- Submit two table metrics for one metropolitan zone with distinct idempotency
  keys, poll them to terminal state, and verify descriptors contain stable run,
  column, timestamp, and row-identity provenance.
- Replay one equivalent idempotent request and receive the same job without a
  second dispatch; reuse its key for another request and receive a conflict.
- Exhaust the configured fixed-window limit across REST and MCP, verify retry
  metadata and absence of rejected-job side effects, then verify acceptance
  after the window resets.
- Use only an MCP download handoff and `LYRA_AGENT_API_KEY` to retrieve JSONL
  from its absolute URL; verify missing/invalid credentials fail.
- Join the two downloaded tables on the descriptor-declared index field and
  confirm external code can select numeric columns and units from descriptor
  metadata without querying the live catalog again.
- Refresh or replace the live catalog after submission and confirm stored
  descriptor provenance remains unchanged apart from lifetime countdown.
- Expire a result and confirm MCP returns an actionable rerun-required error.

## Regression checks

- Existing worker table validation still enforces input feature IDs, declared
  column order, scalar types, nullability, and finite numbers.
- File, failed, and cancelled terminal results retain coherent descriptor and
  download behavior.
- Admin authentication and observability remain separate from agent access.
- Agent credentials never appear in logs, config summaries, descriptors, URLs,
  tool text, or exceptions.
- No old credential name, custom MCP dispatcher, public job access, relative
  download handoff, or pass-through-only idempotency behavior remains.
- No compatibility shim, migration, or dual old/new behavior was introduced.
- The `[tool.ruff.lint]` section in `pyproject.toml` is unchanged.

## Services and cleanup

Use isolated Redis and fake worker/MCP backends for deterministic automated
coverage. If manual validation starts Redis, API, worker, MCP Inspector, or docs
services, stop them and remove temporary result files when validation finishes.
Do not leave long-running processes active.

## Clear pass/fail criteria

Pass only when all manifest validations and the documentation build succeed,
the end-to-end scenarios satisfy the stated auth and data invariants, concurrent
idempotency dispatches once, rate limiting cannot be bypassed through either
entry point, and raw tables are retrievable externally without exposing secrets.
