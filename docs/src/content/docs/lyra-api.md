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

Both clients return models from `lyra-sdk`, such as `MetricInfoV2`,
`JobCreateResponse`, `JobEvent`, `JobStatusInfo`, and `JobResult`.

## Client Configuration

Pass the host name and optional port without a URL scheme. Use `secure=False`
for a local HTTP server.

```python
client = LyraAPIClient("localhost:5219", secure=False, timeout=30.0)
```

Constructor options:

| Option | Purpose |
| --- | --- |
| `host` | Host name plus optional port, such as `localhost:5219`. |
| `timeout` | Request timeout in seconds. |
| `headers` | Default HTTP headers, such as authorization headers. |
| `secure` | `True` uses HTTPS; `False` uses HTTP. |
| `log_level` | Python logging level for client loggers. |

## Synchronous Client

Use `LyraAPIClient` when your caller is synchronous.

```python
from lyra.api import LyraAPIClient

client = LyraAPIClient("localhost:5219", secure=False)

metrics = client.get_metrics()
metric_name = metrics[0].name

job = client.create_job(metric_name, {})

for event in client.iter_job_events(job.job_id):
    if event.event in {"succeeded", "failed", "cancelled"}:
        break

result = client.get_job_result(job.job_id)
print(result.status, result.result)
```

## Asynchronous Client

Use `AsyncLyraAPIClient` when your caller is already async.

```python
import asyncio

from lyra.api import AsyncLyraAPIClient


async def main() -> None:
    client = AsyncLyraAPIClient("localhost:5219", secure=False)

    metrics = await client.get_metrics()
    metric_name = metrics[0].name

    job = await client.create_job(metric_name, {})

    async for event in client.iter_job_events(job.job_id):
        if event.event in {"succeeded", "failed", "cancelled"}:
            break

    result = await client.get_job_result(job.job_id)
    print(result.status, result.result)


asyncio.run(main())
```

## Discovery Methods

| Method | Returns | Use when |
| --- | --- | --- |
| `get_data_types()` | `list[dict[str, Any]]` | You need the supported explicit input wrapper types from `/data_types`. |
| `get_metrics()` | `list[MetricInfoV2]` | You need all metric names, descriptions, request schemas, and result schemas. |
| `get_metrics(metric_name)` | `MetricInfoV2` | You need one metric's schema metadata. |

Fetch metric schemas before submitting jobs. The `input` object passed to
`create_job()` must match the chosen metric's `request_schema`.

## Job Methods

| Method | Returns | Use when |
| --- | --- | --- |
| `create_job(metric, payload, idempotency_key=None)` | `JobCreateResponse` | Submit a job and receive a `job_id`. |
| `get_job(job_id)` | `JobStatusInfo` | Poll the latest status snapshot. |
| `iter_job_events(job_id, last_event_id=None)` | Iterator or async iterator of `JobEvent` | Stream progress and terminal events. |
| `get_job_result(job_id)` | `JobResult` | Fetch a terminal JSON result. |
| `download_job_result_to_file(job_id, path)` | `None` | Download a terminal file result. |

`iter_job_events()` accepts `last_event_id` and sends it as the
`Last-Event-ID` header so a caller can resume an event stream after reconnecting.

`get_job_result()` expects a JSON response. If the job produced a file, call
`download_job_result_to_file()` instead.

## Convenience Methods

For JSON-producing metrics, `process()` submits a job, waits for a terminal
event, fetches the result, and returns `JobResult.result`.

```python
value = client.process(metric_name, payload)
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
    result = client.process("metric_name", {})
except DownloadError as exc:
    print(f"Lyra request failed: {exc}")
```

