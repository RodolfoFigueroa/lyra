# Persist Immutable Job Provenance

## Goal

Persist the validated public run context needed to identify and reproduce a job
without storing resolved geometries.

## Background from the discussion

Current status and result records do not retain the catalog fingerprint, plugin
identity, original metropolitan-zone wrapper, parameters, or output contract.
An external process holding only a result reference must maintain that context
itself.

## Scope

- Add strict SDK models for immutable run provenance and row identity metadata.
- Capture metric name, catalog fingerprint, plugin name/version, validated
  unresolved input, output declaration, and creation timestamp at submission.
- Derive explicit spatial identity metadata during resolution when authoritative
  source information is available.
- Persist provenance under the job lifetime and retrieve it asynchronously and
  synchronously.
- Delete or expire provenance consistently with the job-store lifecycle.

## Out of scope

- Expose new fields in result descriptors.
- Persist resolved GeoJSON or arbitrary worker-local state.
- Invent a feature namespace or version for user GeoJSON.
- Durable storage beyond Redis TTL.

## Files or areas likely affected

- `packages/lyra_sdk/src/lyra/sdk/models/job.py`
- SDK model exports.
- `lyra_app/job_store.py`
- `lyra_app/registry.py`
- `lyra_app/routes/jobs.py`
- `lyra_app/spatial_inputs.py`
- Job-store, job-route, and registry tests.

## Required behavior

- Every accepted job stores one immutable provenance record before dispatch.
- The record contains the validated unresolved public request, never resolved
  GeoJSON.
- Metropolitan-zone location resolution identifies the result features as INEGI
  2020 AGEB `cvegeo` values through explicit metadata.
- Unknown or user-provided feature identities omit optional namespace/version
  fields rather than guessing.
- Catalog and plugin identity reflect the contract used at submission even if
  the live catalog later refreshes.
- Provenance retention is never shorter than status or result retention.
- Missing legacy-shaped records need no migration or fallback.

## Implementation notes

- Capture registry metadata before any catalog can refresh, then store a strict
  serialized model.
- Keep provenance separate from `JobEnvelope.input`, which workers need in
  resolved form.
- Centralize Redis key and TTL handling with existing job-store helpers.

## Tests and verification

- Use the manifest-declared provenance tests.
- Test exact stored fields, absence of geometry, TTL behavior, catalog refresh
  stability, authoritative row identity, and omission for unknown identity.

## Step exit checklist

- [ ] Accepted jobs persist strict immutable provenance.
- [ ] Provenance never stores resolved geometry.
- [ ] Catalog and plugin identity survive catalog refreshes.
- [ ] Known row identity is explicit and unknown identity is omitted.
- [ ] TTL and missing-record behavior are tested.

## Decision gate before the next step

Proceed only when persistence tests prove provenance is stable, compact, and
TTL-aligned.

## Next-step context

The next step composes the stored provenance with terminal results and expanded
column contracts.
