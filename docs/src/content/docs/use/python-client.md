---
title: Python Client
description: Inspect the catalog and submit metric jobs with synchronous or asynchronous clients.
---

Install `lyra-api` and use `LyraClient` or `AsyncLyraClient` to inspect the
catalog, submit dictionary arguments, and retrieve typed job results:

```bash
uv add lyra-api
```

Client classes, job handles, options, exceptions, and `parse_result_ref` are
conveniently available from `lyra.api`. The [Python reference](../../api/lyra/)
documents each definition in its owning module. Import SDK models from their
owning modules, such as `lyra.sdk.models.job` and `lyra.sdk.models.spatial`.

## Inspect the catalog

```python
import os

from lyra.api import LyraClient

client = LyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)

catalog = client.catalog.metrics()
for metric in catalog.metrics:
    print(metric.name, metric.description)

# Use a metric name advertised by your deployment.
metric = client.catalog.metric("population")
print(metric.request_schema)
print(metric.output)
```

The catalog exposes each metric's request schema, spatial inputs, and output
contract, together with a schema dialect, contract version, and catalog
fingerprint. Choose argument names and values from the deployed metric's schema.

## Submit, wait, and retrieve results

The following examples assume the deployment provides a `population` metric
with a `location` field accepting metropolitan-zone codes.

```python
from lyra.api import SubmitOptions
from lyra.sdk.models.job import FileJobResult, TableJobResult
from lyra.sdk.types import JsonObject

arguments: JsonObject = {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
}
handle = client.raw.submit(
    "population",
    arguments,
    options=SubmitOptions(idempotency_key="population-2026-07"),
)
print(handle.job_id, handle.status().status)
result = handle.wait(timeout=300)

if isinstance(result, TableJobResult):
    frame = client.results.dataframe(handle.job_id)
elif isinstance(result, FileJobResult):
    client.results.download_file(handle.job_id, "result.bin")
```

`submit()` returns a `JobHandle`. Its `wait()` method polls status until
completion and returns the successful
table or file result. Once the job is complete, `handle.result()` fetches its
result again. Failed or cancelled jobs raise `MetricRunError` through these
handle methods, with the job ID, status, structured error, and terminal result.
A wait deadline raises `JobWaitTimeoutError`; the job can still be running.

For a saved job ID, use `client.jobs.get(job_id)` to inspect status,
`client.results.get(job_id)` to fetch any terminal result, and
`client.results.descriptor(job_id)` for result metadata. Descriptor and dataframe
operations also accept stable `lyra://results/{job_id}` references.

To submit and wait in one call:

```python
from lyra.api import RunOptions

result = client.raw.run(
    "population",
    arguments,
    options=RunOptions(timeout=300),
)
```

For a metric whose output is a file, `client.raw.run_to_file(metric_name,
arguments, path="result.bin", options=RunOptions(timeout=300))` also downloads
the successful result.

## Asynchronous use

```python
import os

from lyra.api import AsyncLyraClient

client = AsyncLyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)
catalog = await client.catalog.metrics()
handle = await client.raw.submit(
    "population",
    {"location": {"data_type": "met_zone_code", "value": "09.01"}},
)
result = await handle.wait(timeout=300)
```

Await asynchronous catalog, submission, result, and download operations. An
`AsyncJobHandle` provides `status()`, `result()`, and `wait()` as awaitable
operations.

## Errors and polling

Both clients raise `DownloadError` for transport failures, unexpected HTTP
statuses, malformed JSON, and responses that do not match the expected model.
The message identifies the operation; parsing, validation, and transport errors
retain their original exception as the cause. Ordinary requests, including job
submission, are never retried automatically.

An unexpected HTTP 503 with structured service details raises
`ServiceUnavailableError`. Its `code`, `retryable`, and `retry_after_seconds`
attributes provide retry guidance. `Retry-After` accepts nonnegative seconds
or an HTTP date; absent or invalid guidance produces `None`. Unstructured or malformed 503 responses raise `DownloadError`.
`health.readiness()` accepts both HTTP 200 and HTTP 503 and returns the validated
readiness response.

`wait()` polls immediately, then every five seconds with ±10% jitter. Pass a
positive finite `poll_interval` to `wait()` or `RunOptions` to change the cadence.
`timeout=None` waits indefinitely; zero expires immediately. Finite monotonic
deadlines bound observations, retries, sleeps, and terminal-result retrieval.
Timing out or cancelling an async wait never cancels the remote job.

Waiting retries connection failures, timeouts, and HTTP 429/500/502/503/504 up to
five consecutive times, resetting after a successful response. Backoff starts
at one second and doubles up to 30 seconds with jitter. Valid `Retry-After`
guidance takes precedence. Explicitly non-retryable service errors,
authentication failures, other client errors, invalid payloads, and certificate
or configuration errors fail immediately. Exhaustion raises `JobPollingError`;
deadline expiry raises `JobWaitTimeoutError`, both with job context. Missing
jobs or results fail explicitly and never trigger resubmission.

`on_progress` receives the first available `JobProgress` snapshot and subsequent
content changes, ignoring timestamp-only changes. Async handles await callbacks
that return awaitables. Callback exceptions propagate unchanged.

File downloads reject JSON terminal results before opening the destination.
`results.download(ref, path, format="jsonl")` streams table data without pandas;
`results.dataframe(ref)` requires pandas and removes its temporary JSONL file on
success or failure.

## Validation and migration

Metric-specific client generation has been removed. Metric-specific
autocomplete, generated request models, local generated-client validation, and
automatic catalog compatibility checks are no longer available. Pass JSON
objects to the regular client's `raw` methods. Server-side validation remains
authoritative and validates submissions against each metric's request schema.
The catalog endpoints, schemas, and fingerprints remain available for inspection.

## Authentication and administrator clients

Pass a hostname without a URL scheme. HTTPS is enabled by default; for a local
HTTP deployment use `LyraClient("localhost:8000", secure=False, ...)`.
Consumer job operations use `agent_api_key`; public catalog and lookup endpoints
do not require that credential. Keep credentials in environment variables.

Operator applications use a separate client type and administrator credential:

```python
import os

from lyra.api import LyraAdminClient

admin = LyraAdminClient(
    "lyra.example.com",
    admin_api_key=os.environ["LYRA_ADMIN_API_KEY"],
)
status = admin.status()
jobs = admin.jobs.list(status="running")
workers = admin.workers.list()
```

Use `AsyncLyraAdminClient` for the equivalent asynchronous interface. Both
administrator clients expose `health`, `jobs`, `plugin_repos`, `catalog`,
`workers`, `queues`, and `routing`. Consumer and administrator credentials are
accepted by their respective client types.
