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
job = client.create_job(
    metric.name,
    payload,
    idempotency_key="client-operation-1",
)

for event in client.iter_job_events(job.job_id):
    if event.event in {"succeeded", "failed", "cancelled"}:
        break

descriptor = client.get_result_descriptor(job.job_id)
print(descriptor.result_ref, descriptor.status)
```

Replace placeholders from the selected metric's live schema. Do not hard-code
payloads from a different deployment.

## Results

Use `get_job_result()` for the terminal SDK model. Use `download_result()` for
table JSONL and `download_job_result_to_file()` for a file-producing metric.
When pandas is installed locally, `result_dataframe()` hydrates retained table
JSONL in the client process.

`process()` and `process_to_file()` combine submission, event waiting, and
terminal retrieval. Pass explicit idempotency keys to both for retry-safe work.

## Asynchronous client

`AsyncLyraAPIClient` exposes the same operation names. Await ordinary methods
and consume `iter_job_events()` with `async for`. Prefer one client style per
application boundary rather than wrapping the synchronous client in an event
loop.

## Operator calls

Create a separate client with `admin_api_key` for plugin repositories, catalog
refresh, worker restart, routing, observability, job listing, and cancellation.
Never configure the same external process with both keys unless it is an
explicit operator application.
