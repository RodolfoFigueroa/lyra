---
title: Python Client
description: Use the sync and async lyra-api clients with metrics, jobs, events, table results, and file results.
---

The `lyra-api` package wraps Lyra's HTTP job API. The clients return SDK model objects from `lyra-sdk`.

This page shows the common workflow. For the full method reference, constructor
options, exceptions, and sync/async parity, see [lyra-api](../lyra-api/).

## Sync Client

```python
from lyra.api import LyraAPIClient

client = LyraAPIClient("localhost:5219", secure=False)
metrics = client.get_metrics()
metric_name = metrics[0].name
payload = {
    "SPATIAL_FIELD": {
        "data_type": "cvegeo_list",
        "value": ["090020001"],
    }
}

job = client.create_job(metric_name, payload)
status = client.get_job(job.job_id)
```

## Stream Events

```python
terminal_events = {"succeeded", "failed", "cancelled"}

for event in client.iter_job_events(job.job_id):
    print(event.event, event.data)
    if event.event in terminal_events:
        break
```

`iter_job_events()` accepts `last_event_id` to resume from a known SSE stream ID.

## Fetch Table Results

```python
result = client.get_job_result(job.job_id)

if result.status == "succeeded":
    print(result.index, result.columns, result.data)
else:
    print(result.error)
```

Failed and cancelled jobs return terminal JSON with `kind: "failed"` or
`kind: "cancelled"`.

## Download File Results

```python
client.download_job_result_to_file(job.job_id, "result.tif")
```

If a job returns table JSON instead of a file, the client raises a client error for file download calls.

## Convenience Methods

For table-producing metrics:

```python
table = client.process(metric_name, payload)
```

For file-producing metrics:

```python
client.process_to_file(metric_name, payload, "result.tif")
```

Both methods create a job, consume events until a terminal event, then fetch the terminal result.

## Async Client

```python
import asyncio

from lyra.api import AsyncLyraAPIClient


async def main() -> None:
    client = AsyncLyraAPIClient("localhost:5219", secure=False)
    metrics = await client.get_metrics()
    metric_name = metrics[0].name
    payload = {
        "SPATIAL_FIELD": {
            "data_type": "cvegeo_list",
            "value": ["090020001"],
        }
    }
    job = await client.create_job(metric_name, payload)

    async for event in client.iter_job_events(job.job_id):
        if event.event in {"succeeded", "failed", "cancelled"}:
            break

    result = await client.get_job_result(job.job_id)
    print(result.status, result.data)


asyncio.run(main())
```

## Metric Payloads

Avoid hard-coding example payloads from these docs. Fetch the metric's
`request_schema` and submit an `input` object that matches it; every metric has
at least one required spatial wrapper field.
