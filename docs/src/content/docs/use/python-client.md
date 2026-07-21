---
title: Python Client
description: Call Lyra with the synchronous or asynchronous lyra-api client.
---

`lyra-api` wraps discovery, jobs, events, descriptors, downloads, and operator
routes while returning models from `lyra-sdk`. Use the generated
[Python reference](../../reference/generated/python/) for exact signatures.

## Synchronous workflow

```python
import os

from lyra.api import LyraAPIClient

client = LyraAPIClient(
    "localhost:5219",
    secure=False,
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)

metric = client.get_metric("METRIC_NAME")
payload = {
    "SPATIAL_FIELD": {
        "data_type": "met_zone_code",
        "value": "09.01",
    }
}
job = client.submit_job(
    metric.name,
    payload,
    idempotency_key="client-operation-1",
)

def show_progress(event):
    print(event.stage, event.current, event.total)

result = job.wait(timeout=300, on_progress=show_progress)
descriptor = client.get_result_descriptor(job.job_id)
print(descriptor.result_ref, descriptor.status)
```

Replace placeholders from the selected metric's live schema. Do not hard-code
payloads from a different deployment.

## Results

`submit_job()` returns a `JobHandle`; `create_job()` remains the lower-level call
that returns only the submission response. A handle exposes `status()`, resumable
`events()`, `wait()`, and `result()`. Event iteration yields `JobEventRecord`
objects containing the SSE `id` and a typed `lifecycle`, `progress`, or `message`
event. `wait()` accepts callbacks for all records, progress, and messages.

Event streams reconnect automatically with bounded jittered backoff and the last
observed event ID. A wait deadline raises `JobWaitTimeoutError`; exhausted
reconnects raise `JobEventStreamError`; a cursor older than retained history
raises `JobEventCursorGapError`.

Use `get_job_result()` for the terminal SDK model. Use `download_result()` for
table JSONL and `download_job_result_to_file()` for a file-producing metric.
When pandas is installed locally, `result_dataframe()` hydrates retained table
JSONL in the client process.

`process()` and `process_to_file()` combine submission, event waiting, and
terminal retrieval. Pass explicit idempotency keys to both for retry-safe work.

## Asynchronous client

`AsyncLyraAPIClient.submit_job()` returns an `AsyncJobHandle`. Await ordinary
methods, await `wait()` and `result()`, and consume `events()` or
`iter_job_events()` with `async for`. Async callbacks may be regular functions
or awaitables. Prefer one client style per application boundary rather than
wrapping the synchronous client in an event loop.

## Operator calls

Create a separate client with `admin_api_key` for plugin repositories, catalog
refresh, worker restart, routing, observability, job listing, and cancellation.
Never configure the same external process with both keys unless it is an
explicit operator application.
