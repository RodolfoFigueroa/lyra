---
title: AI Agent Guide
description: Stable source map, contracts, commands, and current behavior for AI agents working on Lyra.
---

This page is optimized for agents that need to inspect, edit, or reason about Lyra.

## Route And Credential Boundary

| Access | Routes | Authentication |
| --- | --- | --- |
| Public discovery | `GET /health`, `GET /data-types`, `GET /metrics`, `GET /metrics/{metric_name}`, `GET /lookups/met-zones` | None |
| Agent execution | `POST /jobs`; every status, event, terminal JSON, descriptor, JSONL, and file route under `/jobs/{job_id}`; configured MCP mount | `Authorization: Bearer $LYRA_AGENT_API_KEY` |
| Administration | Every `/admin/*` plugin, catalog, worker, queue, job-list, cancellation, and routing route | `Authorization: Bearer $LYRA_ADMIN_API_KEY` |

The agent key does not authorize admin routes, and the admin key is not an agent
credential. Never configure an external agent with the admin key.

## Source Map

| Task | Start Here |
| --- | --- |
| Agent-authenticated job routes | `lyra_app/routes/jobs.py` |
| Shared submission, idempotency, and limits | `lyra_app/job_submission.py` and `lyra_app/job_store.py` |
| Agent authentication | `lyra_app/agent_auth.py` |
| Metric catalog and payload validation | `lyra_app/registry.py` |
| Redis job status, result, and event store | `lyra_app/job_store.py` |
| Generic Celery worker execution | `lyra_app/worker.py` |
| Plugin sync and install helpers | `lyra_app/plugins.py` |
| Admin plugin routes | `lyra_app/routes/admin.py` |
| SDK job and API models | `packages/lyra_sdk/src/lyra/sdk/models/job.py` |
| SDK manifest models | `packages/lyra_sdk/src/lyra/sdk/models/plugin_v3.py` |
| Runner context protocol | `packages/lyra_sdk/src/lyra/sdk/context.py` |
| Sync Python client | `packages/lyra_api/src/lyra/api/client/sync.py` |
| Async Python client | `packages/lyra_api/src/lyra/api/client/async_.py` |
| MCP transport and strict tools | `packages/lyra_mcp/src/lyra/mcp/server.py`, `models.py`, and `tools.py` |
| Compose deployment examples | `docker/docker-compose.yml` and `docker/docker-compose-dev.yml` |
| Documentation site | `docs/` |

## Contracts

Plugin manifests are `PluginManifestV3` with integer `schema_version: 3`.
Plugin authors write semantic `inputs`; Lyra compiles them into effective JSON
Schema for `/metrics` and `POST /jobs`.

`GET /metrics` returns a `MetricCatalogResponse` with `catalog_fingerprint` and
`metrics`. The fingerprint changes only when the public metric contract changes:
metric names, descriptions, request schemas, or output declarations. It ignores
worker queues, plugin repo ids, entrypoints, and job state.

Metric entrypoints are sync functions shaped as:

```python
def run(job: JobEnvelope, context: RunContext) -> TableJobResult | FileJobResult:
    ...
```

The only Celery task name for metric execution is `lyra.run_metric`.

Terminal result models include `TableJobResult`, `FileJobResult`,
`FailedJobResult`, and `CancelledJobResult`.

Submission captures immutable `JobRunProvenance`: metric, catalog fingerprint,
plugin identity, validated unresolved input, output declaration, creation time,
and row identity when known. Descriptors add concrete column contracts, the
synthetic index field, summary, preview, and remaining lifetime.

Job lifecycle status can be `queued`, `started`, `progress`, `succeeded`, `failed`, or `cancelled`.

For spatial plugins, read [Spatial Plugin Inputs](../spatial-plugin-inputs/).
Every metric manifest declares required spatial inputs with `kind: "location"`
or `kind: "bounds"`. The API exposes compiled wrapper schemas in `/metrics`,
validates client wrappers, and resolves them to GeoJSON dictionaries before
workers receive `JobEnvelope.input`.

## Commands

Prefer focused commands while iterating:

```bash
uv run pytest tests/test_jobs_route.py
uv run ty check
uv run ruff check path/to/file.py
npm run check --prefix docs
npm run build --prefix docs
```

Run broader checks before handoff:

```bash
uv run pytest
uv run ty check
uv run ruff check
npm audit --prefix docs
```

## Editing Expectations

Keep docs and public contracts synchronized. If route behavior, SDK models, plugin manifest fields, worker execution, or client behavior changes, update the relevant docs in the same change.

Use source code as authority over examples. Replace placeholder metric names and payloads with values from the active `/metrics` catalog when testing a live deployment.

Avoid inferring runtime plugin availability from this repository alone. Metrics
come from plugin sources stored in `/lyra_data/state/plugins.toml` and managed
through `/admin/plugin-repos`. Supported source forms include GitHub entries,
explicit `file://` local git repositories, and development `dir://` directory
snapshots. Raw filesystem paths are not supported.

## MCP Agent Surface

The Lyra MCP server uses the official Python MCP SDK's stateless Streamable HTTP
transport and strict input/output JSON Schema contracts. See [MCP Agent
Bridge](../mcp-agent-bridge/) for setup and analysis examples.

- `lyra_lookup_met_zone`
- `lyra_search_metrics`
- `lyra_get_metric`
- `lyra_run_metric`
- `lyra_get_job_result`
- `lyra_get_result_metadata`
- `lyra_get_result_preview`
- `lyra_download_result`

The sequence is lookup, search, inspect, run with an idempotency key, poll one
reference, inspect provenance, then request an absolute authenticated JSONL
handoff. `lyra_run_metric` accepts only a raw metropolitan zone code as
`met_zone_code`; do not pass raw
GeoJSON, census tract lists, or the metric's spatial field in `parameters`.

MCP result tools accept `lyra://results/{job_id}` references. Running jobs
return `status: "running"` with `poll_after_seconds` and
`next_tool: "lyra_get_job_result"`. Agents should wait, call that tool with the
same result reference, and avoid rerunning the metric unless the result expired
or the user explicitly asks for a fresh run. Terminal jobs return compact
descriptors, previews, metadata, or JSONL download handoff metadata instead of
inlining full raw tables.

Equivalent retries reuse the original job without consuming the shared quota.
New REST and MCP submissions default to 10 per 60 seconds. Rate-limited callers
wait the advertised interval and reuse the key. Results expire with the
job-store TTL, so clients inspect lifetime and download promptly.

Lyra runs metrics and exposes retained results. It does not provide SQL or
statistical analysis tools through this MCP feature; clients perform arbitrary
analysis after downloading result tables.
