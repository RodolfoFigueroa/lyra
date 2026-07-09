# Lyra MCP Agent Bridge Roadmap

This roadmap turns Lyra's existing metric catalog and job API into a stable MCP
surface for coding agents and other agent runtimes.

## Agreed Direction

- Expose a small stable MCP surface, not one tool per metric.
- Use `lyra://results/{job_id}` as the v1 result reference shape.
- Reuse the existing Redis-backed job store TTL for result lifetime.
- Always return a descriptor with summary, preview, and raw-access metadata.
- Do not vary the response shape by table size.
- Support raw metropolitan zone codes only for MCP v1.
- Keep raw GeoJSON and explicit census tract lists out of the v1 MCP surface.
- Accept non-spatial metric arguments under a generic `parameters` object.
- Make `lyra_run_metric` wait briefly, then return either a final descriptor or
  a `running` response with `poll_after_seconds` and `next_tool`.
- Keep statistical analysis, joins, regressions, and plotting on the consumer
  side. Lyra should not become a SQL or statistics platform.
- Start metric search with derived lexical metadata from the existing catalog.
- Add optional plugin-authored search metadata later only if derived metadata is
  not enough.
- Extend `lyra-api` with result-reference helpers before creating a separate
  analysis package.

## Target Tool Surface

The planned MCP surface is:

- `lyra_search_metrics`
- `lyra_get_metric`
- `lyra_run_metric`
- `lyra_get_job_result`
- `lyra_get_result_metadata`
- `lyra_get_result_preview`
- `lyra_download_result`

The tool descriptions and result payloads must teach agents the continuation
workflow. A model should not need prior knowledge of Lyra jobs to poll a running
result correctly.

## Result Contract Shape

The client-facing descriptor is JSON. Raw tables are downloaded separately.
JSONL is the required v1 raw export format. CSV and Parquet are deferred.

The descriptor should include:

- `schema_version`
- `result_ref`
- `job_id`
- lifecycle status
- metric name, catalog fingerprint, and plugin identity where available
- Redis-backed lifetime fields such as `expires_at` and `expires_in_seconds`
- met-zone input metadata
- row identity metadata such as `index_name`, `feature_id_namespace`, and
  `feature_id_version` when available
- table columns, row count, preview rows, summary statistics, and raw access
  metadata

## Assumptions

- The first implementation may add new SDK models and public HTTP routes.
- The first MCP server may be mounted under the Lyra API process at `/mcp` or
  shipped as a first-party package that can be mounted there. Prefer the same
  origin as the Lyra API unless the implementation library makes that impractical.
- The first MCP auth mechanism should be bearer-token based. OAuth and signed
  result URLs are deferred.
- Existing plugin runner return models remain valid. The new descriptor is a
  client-facing layer over stored terminal results.
- Test fixtures may use existing smoke plugin metrics instead of real Mexico
  City metrics.

## Rejected Approaches

- Dynamic per-metric tools as the default agent experience.
- Server-side SQL for arbitrary table operations.
- Server-side correlation, regression, or general statistical tool families.
- Returning full large tables directly in normal MCP tool responses.
- Supporting raw GeoJSON in the first MCP interface.
