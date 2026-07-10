---
title: lyra-api
description: Sync and async Python clients for discovering metrics, submitting jobs, streaming events, and fetching results.
---

`lyra-api` is the Python client package for Lyra's HTTP job API. It is useful
for applications that call Lyra, smoke tests for plugin repositories, and local
development scripts that submit jobs against a running API server.

## Common Imports

```python
from lyra.api import AsyncLyraAPIClient, DownloadError, LyraAPIClient, parse_result_ref
```

Both clients return models from `lyra-sdk`, such as `DataTypesResponse`,
`MetricCatalogResponse`, `MetricInfoV3`, `JobCreateResponse`, `JobEvent`, `JobStatusInfo`,
observability models, plugin operation models, terminal result models, and
`ResultDescriptor`.

## Client Configuration

Pass the host name and optional port without a URL scheme. Use `secure=False`
for a local HTTP server.

```python
import os

from lyra.api import LyraAPIClient

client = LyraAPIClient("localhost:5219", secure=False, timeout=30.0)
agent_client = LyraAPIClient(
    "localhost:5219",
    secure=False,
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)
admin_client = LyraAPIClient(
    "localhost:5219",
    secure=False,
    admin_api_key=os.environ["LYRA_ADMIN_API_KEY"],
)
```

The unauthenticated client is only for discovery. The agent client is required
for every job, event, descriptor, and download method. The admin client is for
`/admin/*`; never give its credential to an external agent.

Constructor options:

| Option | Purpose |
| --- | --- |
| `host` | Host name plus optional port, such as `localhost:5219`. |
| `timeout` | Request timeout in seconds. |
| `headers` | Default HTTP headers, such as authorization headers. |
| `agent_api_key` | Agent Bearer token for job and result routes. |
| `admin_api_key` | Explicit admin bearer token. The client sends it as `Authorization: Bearer ...`. |
| `secure` | `True` uses HTTPS; `False` uses HTTP. |
| `log_level` | Python logging level for client loggers. |

## Client Classes

Use `LyraAPIClient` when your caller is synchronous.

```python
from lyra.api import LyraAPIClient

client = LyraAPIClient("localhost:5219", secure=False)
catalog = client.get_metrics()
metrics = catalog.metrics
```

Use `AsyncLyraAPIClient` when your caller is already async.

```python
from lyra.api import AsyncLyraAPIClient

async def get_metrics() -> None:
    client = AsyncLyraAPIClient("localhost:5219", secure=False)
    catalog = await client.get_metrics()
    metrics = catalog.metrics
```

Both clients expose the same method names. Async client methods are awaited,
and `iter_job_events()` is consumed as an async iterator. For a complete
submit, wait, and result workflow, see [Python Client](../python-client/).

## Discovery And Lookup Methods

| Method | Returns | Use when |
| --- | --- | --- |
| `get_health()` | `HealthResponse` | You need public API and Redis readiness from `/health`. |
| `get_data_types()` | `DataTypesResponse` | You need grouped `location` and `bounds` wrapper schemas from `/data-types`. |
| `get_metrics()` | `MetricCatalogResponse` | You need the public catalog fingerprint plus all metric names, descriptions, request schemas, and output declarations. |
| `get_metric(metric_name)` | `MetricInfoV3` | You need one metric's schema metadata. |
| `get_met_zone_code(name)` | `MetZoneCodeResponse` | You need the metropolitan zone code matching a display name. |

Fetch metric schemas before submitting jobs. The `input` object passed to
`create_job()` must match the chosen metric's compiled `request_schema`. Every
metric has at least one required spatial wrapper field.

`MetricCatalogResponse.catalog_fingerprint` changes only when the public metric
contract changes. It ignores worker queues, plugin repo ids, entrypoints, and
job state.

`get_data_types()` returns a grouped response with `location` and `bounds`
lists. Each item includes `data_type`, `description`, and `wrapper_schema`.
These discovery and lookup methods are public.

## Job Methods

| Method | Returns | Use when |
| --- | --- | --- |
| `create_job(metric, payload, idempotency_key=None)` | `JobCreateResponse` | Submit a job and receive a `job_id`. |
| `get_job(job_id)` | `JobStatusInfo` | Poll the latest status snapshot. |
| `iter_job_events(job_id, last_event_id=None)` | Iterator or async iterator of `JobEvent` | Stream progress and terminal events. |
| `get_job_result(job_id)` | `TerminalJobResult` | Fetch terminal JSON result metadata for table, file, failed, or cancelled jobs. |
| `download_job_result_to_file(job_id, path)` | `None` | Download a terminal file result. |
| `get_result_descriptor(result_ref_or_job_id)` | `ResultDescriptor` | Fetch compact result metadata, preview rows, lifetime, and raw-access links. |
| `download_result(result_ref_or_job_id, path, format="jsonl")` | `None` | Stream a table result as raw JSONL. |
| `result_dataframe(result_ref_or_job_id)` | pandas `DataFrame` | Optionally hydrate table JSONL locally when pandas is installed. |
| `list_admin_jobs(limit=50, status=None, metric=None)` | `JobListResponse` | List recent jobs through the admin API. |
| `cancel_admin_job(job_id)` | `JobCancelResponse` | Request cancellation through the admin API. |

Non-admin job methods require `agent_api_key`; admin list and cancellation
methods require `admin_api_key`. Pass a caller-generated idempotency key and
retain it across retries. An equivalent request returns the original job with
`reused=True`; a conflicting request returns `409`. New REST/MCP jobs share the
configurable default of 10 submissions per 60 seconds. On `429`, wait the
`Retry-After` interval and retry with the same key. Replays consume no quota.

`iter_job_events()` accepts `last_event_id` and sends it as the
`Last-Event-ID` header so a caller can resume an event stream after reconnecting.

`get_job_result()` expects a JSON response. If the job produced a file, it
returns `FileJobResult` metadata. Call `download_job_result_to_file()` to fetch
the file bytes from `/jobs/{job_id}/result/download`.

`get_result_descriptor()`, `download_result()`, and `result_dataframe()` accept
either a raw job id or a stable result reference:

```python
result_ref = "lyra://results/job-1"
job_id = parse_result_ref(result_ref)
descriptor = agent_client.get_result_descriptor(result_ref)
agent_client.download_result(job_id, "job-1.jsonl")
```

`download_result()` currently supports `format="jsonl"` for successful table
results. `result_dataframe()` uses the same JSONL download path and raises
`DownloadError` with install guidance when pandas is not available.

Descriptors expire with the job-store TTL. Inspect `descriptor.lifetime` and
download before its deadline. Provenance captures the metric, catalog
fingerprint, plugin version, validated input, output, creation time, and row
identity at submission.

Local analysis stays outside Lyra. Select concrete numeric columns and validate
authoritative row identity before hydrating two tables:

```python
left_ref = "lyra://results/job-left"
right_ref = "lyra://results/job-right"
left_descriptor = agent_client.get_result_descriptor(left_ref)
right_descriptor = agent_client.get_result_descriptor(right_ref)


def contract(descriptor):
    if descriptor.status != "succeeded" or descriptor.table is None:
        raise ValueError("expected a successful table")
    if descriptor.provenance is None or descriptor.table.row_identity is None:
        raise ValueError("result lacks provenance or row identity")
    numeric = [
        column.name
        for column in descriptor.table.column_contracts
        if column.type in {"number", "integer"}
    ]
    if len(numeric) != 1:
        raise ValueError(f"select one declared numeric column from {numeric}")
    return descriptor.table.row_identity, descriptor.table.index_field, numeric[0]


left_identity, left_index, left_column = contract(left_descriptor)
right_identity, right_index, right_column = contract(right_descriptor)
if left_identity != right_identity:
    raise ValueError("row identities do not match")

left = agent_client.result_dataframe(left_ref)[[left_index, left_column]].rename(
    columns={left_index: "row_id", left_column: "left_value"}
)
right = agent_client.result_dataframe(right_ref)[[right_index, right_column]].rename(
    columns={right_index: "row_id", right_column: "right_value"}
)
joined = left.merge(right, on="row_id", validate="one_to_one")
correlation = joined["left_value"].corr(joined["right_value"])
```

Lyra does not provide SQL or statistical analysis helpers server-side. Use the
descriptor contracts to choose columns, record provenance, then download JSONL
or hydrate dataframes locally.

## Admin And Operator Methods

Admin methods call `/admin/*` routes and require the client to include an admin
Bearer token, either with `admin_api_key=...` or an explicit `Authorization`
header.

| Method | Returns | Use when |
| --- | --- | --- |
| `list_plugin_repos()` | `PluginRepoListResponse` | List configured plugin sources. |
| `create_plugin_repo(source, repo_id=None, enabled=True)` | `CreatePluginRepoResponse` | Add a GitHub, `file://`, or `dir://` plugin source and refresh the runtime catalog. |
| `update_plugin_repo(repo_id, source=None, enabled=None)` | `UpdatePluginRepoResponse` | Update a plugin source or enabled flag and refresh the runtime catalog. |
| `delete_plugin_repo(repo_id)` | `DeletePluginRepoResponse` | Remove a plugin source, remove its owned metric queue config, and refresh the runtime catalog. |
| `sync_plugin_repo(repo_id)` | `SyncPluginRepoResponse` | Sync one enabled plugin source and refresh the runtime catalog. |
| `refresh_plugin_catalog()` | `PluginCatalogRefreshResponse` | Sync enabled sources, reload API catalog metadata, prune stale metric routes, and learn if workers should restart. |
| `restart_workers(timeout=30.0)` | `WorkerRestartResponse` | Ask worker pools to restart after draining active work. |
| `list_plugin_routing()` | `PluginRoutingResponse` | List metric-to-queue assignments. |
| `set_plugin_routing(metric_name, queue)` | `MetricQueueAssignmentResponse` | Assign a metric to a queue. |
| `delete_plugin_routing(metric_name)` | `DeleteMetricQueueResponse` | Remove a metric's explicit queue assignment. |
| `get_admin_status()` | `AdminStatusResponse` | Fetch compact API, Redis, catalog, queue, worker, and job-store status. |
| `get_admin_config_summary()` | `ConfigSummaryResponse` | Fetch secret-free runtime config summary. |
| `get_admin_catalog()` | `CatalogSummaryResponse` | Fetch loaded catalog and plugin source metadata. |
| `get_admin_workers()` | `WorkersResponse` | Fetch configured and observed worker summaries. |
| `get_admin_worker(worker_name)` | `WorkerDetail` | Fetch one configured or observed worker. |
| `get_admin_queues()` | `QueuesResponse` | Fetch queue assignments, consumers, and depth unknown markers. |

## Convenience Methods

For table-producing metrics, `process()` submits a job, waits for a terminal
event, fetches the result, and returns a `TableJobResult`.

```python
table = agent_client.process(metric_name, payload, idempotency_key="table-operation")
rows = table.data
```

For file-producing metrics, `process_to_file()` submits a job, waits for a
successful file result, and writes it to a local path.

```python
agent_client.process_to_file(
    metric_name,
    payload,
    "result.tif",
    idempotency_key="file-operation",
)
```

Both convenience methods raise `DownloadError` if the job fails, is cancelled,
or returns the wrong result type.

## Exceptions

`LyraAPIError` is the base exception for client errors. `DownloadError` is the
current concrete exception raised for HTTP, streaming, job failure, and result
download problems.

```python
import os

from lyra.api import DownloadError, LyraAPIClient

client = LyraAPIClient(
    "localhost:5219",
    secure=False,
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)

try:
    result = client.process(metric_name, payload)
except DownloadError as exc:
    print(f"Lyra request failed: {exc}")
```
