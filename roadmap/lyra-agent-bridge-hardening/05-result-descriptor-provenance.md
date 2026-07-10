# Expose Reproducible Result Descriptors

## Goal

Make every terminal result descriptor self-describing enough for reliable
external analysis and audit.

## Background from the discussion

Descriptors currently expose table shape, preview, summary, and raw paths but
omit the run identity and column semantics required to reproduce or interpret a
result after handoff.

## Scope

- Add an explicit descriptor schema version.
- Attach immutable run provenance and completion time to terminal descriptors.
- Expand static and batch-derived output columns into concrete name, type, unit,
  description, and nullability metadata.
- Attach row-index field, namespace, and version when available.
- Apply the provenance contract consistently to successful table/file results
  and to failed/cancelled results where a run record exists.
- Update REST, MCP, and Python client parsing.

## Out of scope

- Change terminal worker result wire formats.
- Add correlation, join, SQL, or regression helpers.
- Add durable results or new raw formats.

## Files or areas likely affected

- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- `packages/lyra_sdk/src/lyra/sdk/models/plugin_v3.py`
- SDK exports.
- `lyra_app/job_store.py`
- `lyra_app/routes/jobs.py`
- `lyra_app/worker.py`
- `packages/lyra_api` result clients.
- `packages/lyra_mcp` result models and tools.
- Result, runner, API-client, and MCP tests.

## Required behavior

- A descriptor alone identifies the metric contract, plugin version, submitted
  metropolitan zone and parameters, timestamps, columns, units, and row identity
  available for the run.
- Batched column expansion uses one shared implementation also used by worker
  output validation; descriptor and worker expectations cannot diverge.
- Column metadata follows actual terminal column order exactly.
- Failed and cancelled descriptors retain provenance without pretending to have
  successful table columns.
- Catalog refreshes after submission do not alter stored descriptor semantics.
- Descriptor summaries, previews, lifetime fields, and raw references continue
  to describe the stored terminal payload accurately.

## Implementation notes

- Move batch-column expansion from worker-private code into an SDK-level pure
  helper shared by validation and descriptors.
- Derive completion time from the terminal persisted state, not the current
  wall clock at descriptor-read time.
- Use additive nested models within the new schema; no old descriptor parser or
  migration path is required.

## Tests and verification

- Use the manifest-declared result provenance tests.
- Cover static and batched tables, files, failures, cancellations, catalog
  refresh, row namespace presence/absence, SDK parsing, and MCP projections.

## Step exit checklist

- [ ] Result descriptors have an explicit schema version.
- [ ] Provenance and completion timestamps are stable across reads.
- [ ] Expanded column contracts match worker validation exactly.
- [ ] Every terminal result kind has coherent provenance behavior.
- [ ] REST, MCP, and Python clients parse the new contract.

## Decision gate before the next step

Proceed only when a stored descriptor remains identical after a catalog refresh
apart from its decreasing lifetime fields.

## Next-step context

The next step uses the persisted request identity to implement concurrency-safe
idempotent submissions.
