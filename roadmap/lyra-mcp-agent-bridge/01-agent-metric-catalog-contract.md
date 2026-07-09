# Expose Agent-Friendly Metric Catalog Metadata

## Goal

Make Lyra's public metric catalog explicit enough for MCP tools to discover
spatial requirements and build search documents without reverse-engineering JSON
Schema internals.

## Background from the discussion

The MCP bridge should use stable tools and a search-then-inspect-then-run
workflow. For v1, agents can run metrics only with raw metropolitan zone codes,
so the bridge needs to know which request fields are Lyra-owned spatial inputs.

## Scope

- Add public metric metadata for `spatial_inputs`.
- Preserve existing metric names, descriptions, request schemas, outputs, and
  catalog fingerprint semantics.
- Add a derived search document or helper that uses existing catalog fields.
- Update registry and route tests for the new public contract.

## Out of scope

- New plugin manifest metadata fields such as tags or domains.
- Dynamic per-metric MCP tools.
- Any changes to plugin runner entrypoints.

## Files or areas likely affected

- `packages/lyra_sdk/src/lyra/sdk/models/metric.py`
- `lyra_app/registry.py`
- `lyra_app/routes/metrics.py`
- `tests/test_registry_catalog.py`
- `tests/test_metrics_route.py`
- `docs/src/content/docs/metrics-catalog.md`

## Required behavior

- `GET /metrics` and `GET /metrics/{metric_name}` include a machine-readable
  mapping from request field names to spatial kinds, such as
  `{"location": "location"}` or `{"bounds": "bounds"}`.
- The public catalog fingerprint changes when public agent-facing contract
  fields change.
- Existing clients that ignore unknown response fields continue to work.
- Derived search text includes metric name, description, input field names,
  input descriptions when available, output column names, output descriptions,
  units, and output kind.

## Implementation notes

- Prefer adding fields to `MetricInfoV3` rather than building MCP-only
  inference logic.
- Source the spatial mapping from `CompiledMetricManifestV3.spatial_inputs`.
- Keep search metadata derived in Lyra for v1; do not make plugin authors add
  new manifest fields yet.
- Document that optional plugin-authored search metadata is intentionally
  deferred.

## Tests and verification

- Extend registry catalog tests to assert `spatial_inputs` is present for table
  and file metrics.
- Extend fingerprint tests so this public field is included in the contract
  hash.
- Extend metrics route tests for the response shape.

## Step exit checklist

- `MetricInfoV3` exposes spatial metadata.
- Registry emits the new field for all loaded metrics.
- Catalog fingerprint behavior is covered by tests.
- Metrics catalog docs mention the new field and its MCP purpose.

## Decision gate before the next step

Confirm the field name and exact JSON shape are stable enough for MCP and
client packages to consume.

## Next-step context

The next step will define descriptors for completed results. It should consume
the same metric metadata rather than duplicating output column definitions.
