---
title: lyra-api
description: Sync and async Python clients for discovering metrics, submitting jobs, streaming events, and fetching results.
---

`lyra-api` is the Python client package for Lyra's HTTP job API. It is useful
for applications that call Lyra, smoke tests for plugin repositories, and local
development scripts that submit jobs against a running API server.

## Common Imports

```python
from lyra.api import AsyncLyraAPIClient, DownloadError, LyraAPIClient
```

Both clients return models from `lyra-sdk`, such as `DataTypesResponse`,
`MetricInfoV3`, `JobCreateResponse`, `JobEvent`, `JobStatusInfo`,
observability models, plugin operation models, and terminal result models.

## Client Configuration

Pass the host name and optional port without a URL scheme. Use `secure=False`
for a local HTTP server.

```python
client = LyraAPIClient("localhost:5219", secure=False, timeout=30.0)
admin_client = LyraAPIClient(
    "localhost:5219",
    secure=False,
    admin_api_key="admin-secret",
)
```

Constructor options:

| Option | Purpose |
| --- | --- |
| `host` | Host name plus optional port, such as `localhost:5219`. |
| `timeout` | Request timeout in seconds. |
| `headers` | Default HTTP headers, such as authorization headers. |
| `admin_api_key` | Explicit admin bearer token. The client sends it as `Authorization: Bearer ...`. |
| `secure` | `True` uses HTTPS; `False` uses HTTP. |
| `log_level` | Python logging level for client loggers. |

## Client Classes

Use `LyraAPIClient` when your caller is synchronous.

```python
from lyra.api import LyraAPIClient

client = LyraAPIClient("localhost:5219", secure=False)
metrics = client.get_metrics()
```

Use `AsyncLyraAPIClient` when your caller is already async.

```python
from lyra.api import AsyncLyraAPIClient

async def get_metrics() -> None:
    client = AsyncLyraAPIClient("localhost:5219", secure=False)
    metrics = await client.get_metrics()
```

Both clients expose the same method names. Async client methods are awaited,
and `iter_job_events()` is consumed as an async iterator. For a complete
submit, wait, and result workflow, see [Python Client](../python-client/).

## Discovery And Lookup Methods

| Method | Returns | Use when |
| --- | --- | --- |
| `get_health()` | `HealthResponse` | You need public API and Redis readiness from `/health`. |
| `get_data_types()` | `DataTypesResponse` | You need grouped `location` and `bounds` wrapper schemas from `/data-types`. |
| `get_metrics()` | `list[MetricInfoV3]` | You need all metric names, descriptions, request schemas, and output declarations. |
| `get_metrics(metric_name)` | `MetricInfoV3` | You need one metric's schema metadata. |
| `get_met_zone_code(name)` | `MetZoneCodeResponse` | You need the metropolitan zone code matching a display name. |

Fetch metric schemas before submitting jobs. The `input` object passed to
`create_job()` must match the chosen metric's compiled `request_schema`. Every
metric has at least one required spatial wrapper field.

`get_data_types()` returns a grouped response with `location` and `bounds`
lists. Each item includes `data_type`, `description`, and `wrapper_schema`.

## Job Methods

| Method | Returns | Use when |
| --- | --- | --- |
| `create_job(metric, payload, idempotency_key=None)` | `JobCreateResponse` | Submit a job and receive a `job_id`. |
| `get_job(job_id)` | `JobStatusInfo` | Poll the latest status snapshot. |
| `iter_job_events(job_id, last_event_id=None)` | Iterator or async iterator of `JobEvent` | Stream progress and terminal events. |
| `get_job_result(job_id)` | `TerminalJobResult` | Fetch terminal JSON result metadata for table, file, failed, or cancelled jobs. |
| `download_job_result_to_file(job_id, path)` | `None` | Download a terminal file result. |
| `list_admin_jobs(limit=50, status=None, metric=None)` | `JobListResponse` | List recent jobs through the admin API. |
| `cancel_admin_job(job_id)` | `JobCancelResponse` | Request cancellation through the admin API. |

`iter_job_events()` accepts `last_event_id` and sends it as the
`Last-Event-ID` header so a caller can resume an event stream after reconnecting.

`get_job_result()` expects a JSON response. If the job produced a file, it
returns `FileJobResult` metadata. Call `download_job_result_to_file()` to fetch
the file bytes from `/jobs/{job_id}/result/download`.

## Admin And Operator Methods

Admin methods call `/admin/*` routes and require the client to include an admin
Bearer token, either with `admin_api_key=...` or an explicit `Authorization`
header.

| Method | Returns | Use when |
| --- | --- | --- |
| `list_plugin_repos()` | `PluginRepoListResponse` | List configured plugin sources. |
| `create_plugin_repo(source, repo_id=None, enabled=True)` | `PluginRepoResponse` | Add a GitHub, `file://`, or `dir://` plugin source. |
| `update_plugin_repo(repo_id, source=None, enabled=None)` | `PluginRepoResponse` | Update a plugin source or enabled flag. |
| `delete_plugin_repo(repo_id)` | `DeletePluginRepoResponse` | Remove a plugin source and its owned metric queue config from Lyra-owned state. |
| `sync_plugin_repo(repo_id)` | `SyncPluginRepoResponse` | Sync one enabled plugin source. |
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
table = client.process(metric_name, payload)
rows = table.data
```

For file-producing metrics, `process_to_file()` submits a job, waits for a
successful file result, and writes it to a local path.

```python
client.process_to_file(metric_name, payload, "result.tif")
```

Both convenience methods raise `DownloadError` if the job fails, is cancelled,
or returns the wrong result type.

## Exceptions

`LyraAPIError` is the base exception for client errors. `DownloadError` is the
current concrete exception raised for HTTP, streaming, job failure, and result
download problems.

```python
from lyra.api import DownloadError, LyraAPIClient

client = LyraAPIClient("localhost:5219", secure=False)

try:
    result = client.process(metric_name, payload)
except DownloadError as exc:
    print(f"Lyra request failed: {exc}")
```
