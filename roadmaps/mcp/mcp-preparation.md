# MCP Preparation

## Objective

Prepare Lyra for MCP by defining the contracts that agents and plugin authors
will rely on. This phase should happen before protocol implementation so the MCP
adapter has one clear source of truth.

## Current Starting Point

- Indicators are discovered from plugin entry points in `lyra_app.registry`.
- Each plugin must provide `METRIC_DESCRIPTION`.
- Plugins may provide `TAVI_HINT`, which is currently a free-form string.
- Request schemas are generated from plugin function signatures using Pydantic.
- Results are produced by Celery workers and retrieved through
  `/download_result/{download_id}`.

## Contract Work

### Plugin Metadata Contract

Replace or supplement `TAVI_HINT` with a structured metadata object, such as
`METRIC_METADATA` or `AGENT_METADATA`.

Recommended fields:

- `metadata_version`
- `agent_description`
- `use_cases`
- `avoid_when`
- `input_examples`
- `output_summary`
- `output_schema`
- `units`
- `spatial_scope`
- `valid_geographies`
- `temporal_coverage`
- `data_sources`
- `latency_class`
- `cost_class`
- `known_limitations`
- `result_type`
- `returns_file`
- `plugin_version`
- `plugin_source`

The initial version can allow many optional fields, but the contract should make
the intended direction explicit. Agent-facing behavior will only be as good as
this metadata.

### Input Schema Contract

The current Pydantic model generation is a good base. Before exposing it through
MCP, confirm that generated schemas include enough information for agents:

- Field descriptions.
- Units.
- Defaults.
- Required vs optional fields.
- Accepted geometry wrappers.
- CRS assumptions.
- Example values.
- Validation constraints where possible.

Raw type names like `ExplicitLocationAPI` are not enough for arbitrary agents.

### Output Schema Contract

Revive and complete the existing `MetricOutputSchema` concept in
`packages/lyra_sdk/src/lyra/sdk/models/metric.py`.

The output schema should tell agents:

- Whether the result is JSON, tabular data, GeoJSON, raster, or another file.
- Which columns or fields are returned.
- Which field is the primary value.
- Units for numeric outputs.
- Whether values are normalized by area, population, or another denominator.
- Which fields are identifiers, dimensions, metrics, or geometry.
- How to interpret missing, null, or non-finite values.

### Job Lifecycle Contract

Define a stable lifecycle that can be used by both MCP and existing clients:

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `expired`

Include stable error categories:

- `validation`
- `unknown_metric`
- `worker`
- `timeout`
- `cancelled`
- `expired`
- `infrastructure`

### Result Handle Contract

For long-running, large, or binary outputs, tools should return a result handle
instead of embedding the whole output.

The handle should include:

- `job_id`
- `download_id`
- `status`
- `expires_at` if known
- `result_type`
- `mime_type` if known
- `size_hint` if available
- `resource_uri` or API URL if appropriate

### Tool Naming Contract

Normalize metric names into MCP-safe tool names. Prefer names that are stable,
ASCII, and descriptive.

Example patterns:

- `run_tree_coverage`
- `run_temperature_raster`
- `describe_metric`
- `submit_metric`
- `get_metric_result`

Keep a mapping from MCP tool name back to the registry metric name.

### Versioning Contract

Track versions separately:

- Lyra MCP adapter version.
- Plugin metadata contract version.
- MCP protocol version supported.
- Individual plugin version or source ref.

Agents may cache schemas, so version identifiers should be visible in discovery
responses.

## Design Decisions To Make

- Should the first version expose one tool per metric, a generic `run_metric`
  tool, or both?
- Should execution be synchronous for small jobs, asynchronous for all jobs, or
  always handle-based?
- Should plugin metadata be strict from day one, or introduced with warnings?
- Should MCP resources mirror `/metrics` and `/models`, or define a cleaner
  agent-facing resource tree?
- What result TTL is acceptable for agent workflows?

## Exit Criteria

Preparation is complete when:

- The metadata contract is documented.
- The input and output schema expectations are documented.
- Tool naming rules are documented.
- Job and result handle shapes are documented.
- Existing plugin repositories can be assessed against the new metadata
  contract.
- The first MCP implementation has a clear minimal scope.

