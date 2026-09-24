"""Asynchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import asyncio
import inspect
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
    cast,
)

import aiofiles
import aiohttp
from lyra.api.client import endpoints
from lyra.api.client.base import BaseTransport, load_pandas
from lyra.api.client.endpoints import RequestSpec, validate_response
from lyra.api.client.events import (
    RetryableEventError,
    SSEBuffer,
    StreamState,
    terminal_event,
)
from lyra.api.client.results import (
    dataframe_path,
    download_needs_text,
    invoke_callbacks,
    require_file_result,
    successful_result,
    validate_download,
    validate_jsonl_format,
)
from lyra.api.exceptions import (
    DownloadError,
    JobEventStreamError,
)
from lyra.sdk.models.job import (
    FileJobResult,
    JobCancelResponse,
    JobCreateResponse,
    JobEventRecord,
    JobLifecycleStatus,
    JobListResponse,
    JobMessageEvent,
    JobProgressEvent,
    JobStatusInfo,
    ResultDescriptor,
    TableJobResult,
    TerminalJobResult,
)

if TYPE_CHECKING:
    import os
    from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable

    import pandas as pd
    from lyra.api.options import RunOptions, SubmitOptions
    from lyra.sdk.models.admin import PluginRepoListResponse, PluginRoutingResponse
    from lyra.sdk.models.data_types import DataTypesResponse
    from lyra.sdk.models.lookups import MetZoneCodeResponse
    from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfoV4
    from lyra.sdk.models.observability import (
        AdminStatusResponse,
        CatalogSummaryResponse,
        ConfigSummaryResponse,
        LivenessResponse,
        QueuesResponse,
        ReadinessResponse,
        WorkerDetail,
        WorkersResponse,
    )
    from lyra.sdk.types import JsonObject

_ResponseT = TypeVar("_ResponseT")
_SuccessResultT = TypeVar("_SuccessResultT", bound=TableJobResult | FileJobResult)
SuccessfulJobResult = TableJobResult | FileJobResult


async def _aiter_sse_job_events(
    lines: AsyncIterable[bytes],
    state: StreamState,
) -> AsyncIterator[JobEventRecord]:
    buffer = SSEBuffer()
    async for line in lines:
        state.check_deadline()
        record = buffer.add(line)
        if record is not None:
            yield record
    state.check_deadline()
    record = buffer.flush()
    if record is not None:
        yield record


class AsyncJobHandle(Generic[_SuccessResultT]):
    """Observe a submitted job and retrieve its successful result asynchronously.

    Instances are returned by `AsyncLyraClient.raw.submit`; applications
    normally do not construct handles directly.

    Attributes:
        submission: The response returned when the job was submitted.

    """

    def __init__(
        self,
        client: _AsyncTransport,
        submission: JobCreateResponse,
    ) -> None:
        """Initialize a handle for an existing asynchronous job submission."""
        self._client = client
        self.submission = submission

    @property
    def job_id(self) -> str:
        """The submitted job's identifier."""
        return self.submission.job_id

    @property
    def metric(self) -> str:
        """The name of the submitted metric."""
        return self.submission.metric

    async def status(self) -> JobStatusInfo:
        """Fetch the job's current lifecycle status.

        Returns:
            The latest status reported by the API.

        """
        return await self._client.get_job(self.job_id)

    def events(
        self,
        *,
        after_id: str | None = None,
        kinds: set[str] | None = None,
        timeout: float | None = None,
        max_reconnect_attempts: int = 5,
    ) -> AsyncIterator[JobEventRecord]:
        """Stream job events asynchronously until a terminal state is reached.

        The stream reconnects automatically after transient connection failures and
        resumes after the last received event.

        Args:
            after_id: Resume after this server-sent event identifier.
            kinds: Event kinds to yield. All events are yielded when omitted.
            timeout: Maximum total number of seconds to wait. ``None`` waits without
                a deadline.
            max_reconnect_attempts: Number of consecutive reconnection attempts
                allowed after the initial connection.

        Returns:
            An asynchronous iterator of job event records in server order.

        """
        return self._client.iter_job_events(
            self.job_id,
            last_event_id=after_id,
            kinds=kinds,
            timeout=timeout,
            max_reconnect_attempts=max_reconnect_attempts,
        )

    async def result(self) -> _SuccessResultT:
        """Fetch and return the job's successful terminal result.

        Returns:
            The table or file result associated with the job.

        Failed or cancelled jobs raise ``MetricRunError``.

        """
        result = await self._client.get_job_result(self.job_id)
        return cast("_SuccessResultT", successful_result(result))

    def wait(
        self,
        *,
        timeout: float | None = None,
        on_event: Callable[[JobEventRecord], object] | None = None,
        on_progress: Callable[[JobProgressEvent], object] | None = None,
        on_message: Callable[[JobMessageEvent], object] | None = None,
    ) -> Awaitable[_SuccessResultT]:
        """Return an awaitable that waits for and returns the successful result.

        Callbacks may be regular functions or return awaitables; awaitable callback
        results are awaited before the next event is processed.

        Args:
            timeout: Maximum total number of seconds to wait. ``None`` waits without
                a deadline.
            on_event: Called for every event received from the job stream.
            on_progress: Called for every progress event.
            on_message: Called for every message event.

        Returns:
            An awaitable resolving to the table or file result produced by the job.

        """
        return self._wait(
            wait_seconds=timeout,
            on_event=on_event,
            on_progress=on_progress,
            on_message=on_message,
        )

    async def _wait(
        self,
        *,
        wait_seconds: float | None,
        on_event: Callable[[JobEventRecord], object] | None,
        on_progress: Callable[[JobProgressEvent], object] | None,
        on_message: Callable[[JobMessageEvent], object] | None,
    ) -> _SuccessResultT:
        async for record in self.events(timeout=wait_seconds):
            for callback_result in invoke_callbacks(
                record, on_event, on_progress, on_message
            ):
                if inspect.isawaitable(callback_result):
                    await callback_result
            if terminal_event(record):
                return await self.result()
        err = f"Job {self.job_id} event stream ended before a terminal event."
        raise JobEventStreamError(
            err,
            job_id=self.job_id,
            last_event_id=None,
            attempts=0,
        )


class _AsyncTransport(BaseTransport):  # ruff: ignore[too-many-public-methods] -- API surface
    """Private asynchronous HTTP implementation used by resource clients."""

    async def _request(self, spec: RequestSpec[_ResponseT]) -> _ResponseT:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.request(
                    spec.method,
                    self._http_url(spec.path),
                    params=spec.params,
                    json=spec.json_body,
                    headers=self._auth_headers if spec.authenticated else self.headers,
                ) as response,
            ):
                return validate_response(
                    spec,
                    response.status,
                    await response.text(),
                    response.headers.get("content-type", ""),
                    response.headers.get("Retry-After"),
                )
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as exc:
            err = f"Failed to {spec.operation}: request error: {exc}"
            raise DownloadError(err) from exc

    async def get_liveness(self) -> LivenessResponse:
        return await self._request(endpoints.get_liveness())

    async def get_readiness(self) -> ReadinessResponse:
        return await self._request(endpoints.get_readiness())

    async def get_met_zone_code(self, name: str) -> MetZoneCodeResponse:
        return await self._request(endpoints.get_met_zone_code(name))

    async def create_job(
        self,
        metric: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> JobCreateResponse:
        return await self._request(
            endpoints.create_job(metric, payload, idempotency_key=idempotency_key)
        )

    async def submit_job(
        self,
        metric: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> AsyncJobHandle[SuccessfulJobResult]:
        return AsyncJobHandle(
            self,
            await self.create_job(metric, payload, idempotency_key=idempotency_key),
        )

    async def get_job(self, job_id: str) -> JobStatusInfo:
        return await self._request(endpoints.get_job(job_id))

    async def list_admin_jobs(
        self,
        *,
        limit: int = 50,
        status: JobLifecycleStatus | None = None,
        metric: str | None = None,
    ) -> JobListResponse:
        return await self._request(
            endpoints.list_admin_jobs(limit=limit, status=status, metric=metric)
        )

    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        return await self._request(endpoints.cancel_admin_job(job_id))

    async def list_plugin_repos(self) -> PluginRepoListResponse:
        return await self._request(endpoints.list_plugin_repos())

    async def list_plugin_routing(self) -> PluginRoutingResponse:
        return await self._request(endpoints.list_plugin_routing())

    async def get_admin_status(self) -> AdminStatusResponse:
        return await self._request(endpoints.get_admin_status())

    async def get_admin_config_summary(self) -> ConfigSummaryResponse:
        return await self._request(endpoints.get_admin_config_summary())

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        return await self._request(endpoints.get_admin_catalog())

    async def get_admin_workers(self) -> WorkersResponse:
        return await self._request(endpoints.get_admin_workers())

    async def get_admin_worker(self, worker_name: str) -> WorkerDetail:
        return await self._request(endpoints.get_admin_worker(worker_name))

    async def get_admin_queues(self) -> QueuesResponse:
        return await self._request(endpoints.get_admin_queues())

    def iter_job_events(
        self,
        job_id: str,
        *,
        last_event_id: str | None = None,
        kinds: set[str] | None = None,
        timeout: float | None = None,
        max_reconnect_attempts: int = 5,
    ) -> AsyncIterator[JobEventRecord]:
        return self._iter_job_events(
            job_id,
            last_event_id=last_event_id,
            kinds=kinds,
            wait_seconds=timeout,
            max_reconnect_attempts=max_reconnect_attempts,
        )

    async def _iter_job_events(
        self,
        job_id: str,
        *,
        last_event_id: str | None = None,
        kinds: set[str] | None = None,
        wait_seconds: float | None = None,
        max_reconnect_attempts: int = 5,
    ) -> AsyncIterator[JobEventRecord]:
        state = StreamState(
            job_id, last_event_id, kinds, wait_seconds, max_reconnect_attempts
        )
        while True:
            state.check_deadline()
            stream_timeout = aiohttp.ClientTimeout(
                total=None,
                sock_connect=state.read_timeout(self.timeout),
                sock_read=state.read_timeout(self.timeout),
            )
            try:
                async with (
                    aiohttp.ClientSession(timeout=stream_timeout) as session,
                    session.get(
                        self._http_url(f"jobs/{job_id}/events"),
                        headers=state.headers(self._auth_headers),
                    ) as response,
                ):
                    state.validate_status(response.status)
                    async for record in _aiter_sse_job_events(response.content, state):
                        if state.accept(record):
                            yield record  # ruff: ignore[yield-in-context-manager-in-async-generator] -- live response
                        if state.terminal:
                            return
            except (aiohttp.ClientError, TimeoutError, RetryableEventError):
                pass
            await asyncio.sleep(state.retry_delay())

    async def get_job_result(self, job_id: str) -> TerminalJobResult:
        return await self._request(endpoints.get_job_result(job_id))

    async def _download(
        self,
        route: str,
        path: str | os.PathLike[str],
        *,
        operation: str,
        job_id: str | None = None,
    ) -> None:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(route), headers=self._auth_headers
                ) as response,
            ):
                if download_needs_text(
                    response.status, response.headers.get("content-type", ""), job_id
                ):
                    validate_download(
                        response.status,
                        await response.text(),
                        response.headers.get("Retry-After"),
                        operation=operation,
                        job_id=job_id,
                    )
                async with aiofiles.open(path, "wb") as file:
                    async for chunk in response.content.iter_chunked(65536):
                        await file.write(chunk)
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as exc:
            err = f"Failed to {operation}: request error: {exc}"
            raise DownloadError(err) from exc

    async def download_job_result_to_file(
        self,
        job_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        await self._download(
            f"jobs/{job_id}/result/download",
            path,
            operation="download job result",
            job_id=job_id,
        )

    async def get_result_descriptor(
        self,
        result_ref_or_job_id: str,
    ) -> ResultDescriptor:
        return await self._request(
            endpoints.get_result_descriptor(
                self._job_id_from_result_ref(result_ref_or_job_id)
            )
        )

    async def download_result(
        self,
        result_ref_or_job_id: str,
        path: str | os.PathLike[str],
        *,
        format: str = "jsonl",  # ruff:ignore[builtin-argument-shadowing]
    ) -> None:
        validate_jsonl_format(format)
        job_id = self._job_id_from_result_ref(result_ref_or_job_id)
        await self._download(
            f"jobs/{job_id}/result/table.jsonl", path, operation="download result"
        )

    async def result_dataframe(self, result_ref_or_job_id: str) -> pd.DataFrame:
        pandas = load_pandas()
        with dataframe_path() as temp_path:
            await self.download_result(result_ref_or_job_id, temp_path, format="jsonl")
            return pandas.read_json(temp_path, lines=True)

    async def get_data_types(self) -> DataTypesResponse:
        return await self._request(endpoints.get_data_types())

    async def get_metrics(self) -> MetricCatalogResponse:
        return await self._request(endpoints.get_metrics())

    async def get_metric(self, metric_name: str) -> MetricInfoV4:
        return await self._request(endpoints.get_metric(metric_name))


class _HealthResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def liveness(self) -> LivenessResponse:
        return await self._transport.get_liveness()

    async def readiness(self) -> ReadinessResponse:
        return await self._transport.get_readiness()


class _LookupsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def met_zone_code(self, name: str) -> MetZoneCodeResponse:
        return await self._transport.get_met_zone_code(name)


class _CatalogResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def data_types(self) -> DataTypesResponse:
        return await self._transport.get_data_types()

    async def metrics(self) -> MetricCatalogResponse:
        return await self._transport.get_metrics()

    async def metric(self, name: str) -> MetricInfoV4:
        return await self._transport.get_metric(name)


class _JobsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def get(self, job_id: str) -> JobStatusInfo:
        return await self._transport.get_job(job_id)

    def events(
        self,
        job_id: str,
        *,
        after_id: str | None = None,
        kinds: set[str] | None = None,
        timeout: float | None = None,
        max_reconnect_attempts: int = 5,
    ) -> AsyncIterator[JobEventRecord]:
        return self._transport.iter_job_events(
            job_id,
            last_event_id=after_id,
            kinds=kinds,
            timeout=timeout,
            max_reconnect_attempts=max_reconnect_attempts,
        )


class _ResultsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def get(self, job_id: str) -> TerminalJobResult:
        return await self._transport.get_job_result(job_id)

    async def descriptor(self, ref: str) -> ResultDescriptor:
        return await self._transport.get_result_descriptor(ref)

    async def download(
        self,
        ref: str,
        path: str | os.PathLike[str],
        *,
        format: str = "jsonl",  # ruff:ignore[builtin-argument-shadowing]
    ) -> None:
        await self._transport.download_result(ref, path, format=format)

    async def download_file(
        self,
        job_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        await self._transport.download_job_result_to_file(job_id, path)

    async def dataframe(self, ref: str) -> pd.DataFrame:
        return await self._transport.result_dataframe(ref)


class _RawMetricsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def create(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: SubmitOptions | None = None,
    ) -> JobCreateResponse:
        key = options.idempotency_key if options is not None else None
        return await self._transport.create_job(
            metric,
            arguments,
            idempotency_key=key,
        )

    async def submit(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: SubmitOptions | None = None,
    ) -> AsyncJobHandle[SuccessfulJobResult]:
        key = options.idempotency_key if options is not None else None
        return await self._transport.submit_job(
            metric,
            arguments,
            idempotency_key=key,
        )

    async def run(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: RunOptions | None = None,
    ) -> SuccessfulJobResult:
        key = options.idempotency_key if options is not None else None
        wait_seconds = options.timeout if options is not None else None
        handle = await self._transport.submit_job(
            metric,
            arguments,
            idempotency_key=key,
        )
        return await handle.wait(timeout=wait_seconds)

    async def run_to_file(
        self,
        metric: str,
        arguments: JsonObject,
        path: str | os.PathLike[str],
        *,
        options: RunOptions | None = None,
    ) -> None:
        result = await self.run(
            metric,
            arguments,
            options=options,
        )
        require_file_result(result)
        await self._transport.download_job_result_to_file(result.job_id, path)


class _AdminJobsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(
        self,
        *,
        limit: int = 50,
        status: JobLifecycleStatus | None = None,
        metric: str | None = None,
    ) -> JobListResponse:
        return await self._transport.list_admin_jobs(
            limit=limit,
            status=status,
            metric=metric,
        )

    async def cancel(self, job_id: str) -> JobCancelResponse:
        return await self._transport.cancel_admin_job(job_id)


class _AdminPluginReposResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> PluginRepoListResponse:
        return await self._transport.list_plugin_repos()


class _AdminCatalogResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def summary(self) -> CatalogSummaryResponse:
        return await self._transport.get_admin_catalog()


class _AdminWorkersResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> WorkersResponse:
        return await self._transport.get_admin_workers()

    async def get(self, name: str) -> WorkerDetail:
        return await self._transport.get_admin_worker(name)


class _AdminQueuesResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> QueuesResponse:
        return await self._transport.get_admin_queues()


class _AdminRoutingResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> PluginRoutingResponse:
        return await self._transport.list_plugin_routing()


class AsyncLyraClient:
    """Access Lyra's consumer API with asynchronous requests.

    The client groups endpoints into resource namespaces. Use :attr:`catalog` to
    discover metrics, :attr:`raw` to submit or run metrics with dictionary
    arguments, and :attr:`jobs` and :attr:`results` to observe existing jobs.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Default HTTP request timeout in seconds.
        headers: Additional headers included with every request.
        agent_api_key: Bearer token for agent-protected job endpoints. Public
            catalog, lookup, and health endpoints do not require it.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Asynchronous liveness and readiness endpoints.
        lookups: Asynchronous public lookup endpoints.
        catalog: Asynchronous metric and data-type discovery endpoints.
        jobs: Asynchronous job status and event-stream endpoints.
        results: Asynchronous job result inspection and download endpoints.
        raw: Asynchronous untyped metric submission and execution endpoints.

    Example:
        >>> client = AsyncLyraClient("lyra.example.com", agent_api_key="...")
        >>> metrics = await client.catalog.metrics()

    """

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        agent_api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Initialize an asynchronous consumer client and its resources."""
        transport = _AsyncTransport(
            host,
            timeout,
            headers,
            api_key=agent_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        """Asynchronous liveness and readiness endpoints."""
        self.lookups = _LookupsResource(transport)
        """Asynchronous public lookup endpoints."""
        self.catalog = _CatalogResource(transport)
        """Asynchronous metric and data-type discovery endpoints."""
        self.jobs = _JobsResource(transport)
        """Asynchronous job status and event-stream endpoints."""
        self.results = _ResultsResource(transport)
        """Asynchronous job result inspection and download endpoints."""
        self.raw = _RawMetricsResource(transport)
        """Asynchronous untyped metric submission and execution endpoints."""


class AsyncLyraAdminClient:
    """Access Lyra's administrator API with asynchronous requests.

    Administrator credentials are intentionally isolated from
    `AsyncLyraClient`. This client exposes operational state and mutation
    endpoints, plus the public health checks, but does not expose consumer metric
    execution.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Default HTTP request timeout in seconds.
        headers: Additional headers included with every request.
        admin_api_key: Bearer token for administrator endpoints.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Asynchronous liveness and readiness endpoints.
        jobs: Asynchronous administrative job listing and cancellation endpoints.
        plugin_repos: Asynchronous plugin repository configuration and
            synchronization endpoints.
        catalog: Asynchronous administrative catalog summary and refresh endpoints.
        workers: Asynchronous worker inspection and restart endpoints.
        queues: Asynchronous queue inspection endpoints.
        routing: Asynchronous metric-to-queue routing endpoints.

    Example:
        >>> admin = AsyncLyraAdminClient(
        ...     "lyra.example.com", admin_api_key="..."
        ... )
        >>> status = await admin.status()

    """

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        admin_api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Initialize an asynchronous administrator client and its resources."""
        transport = _AsyncTransport(
            host,
            timeout,
            headers,
            api_key=admin_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        """Asynchronous liveness and readiness endpoints."""
        self.jobs = _AdminJobsResource(transport)
        """Asynchronous administrative job listing and cancellation endpoints."""
        self.plugin_repos = _AdminPluginReposResource(transport)
        """Asynchronous plugin repository configuration and synchronization."""
        self.catalog = _AdminCatalogResource(transport)
        """Asynchronous administrative catalog summary and refresh endpoints."""
        self.workers = _AdminWorkersResource(transport)
        """Asynchronous worker inspection and restart endpoints."""
        self.queues = _AdminQueuesResource(transport)
        """Asynchronous queue inspection endpoints."""
        self.routing = _AdminRoutingResource(transport)
        """Asynchronous metric-to-queue routing endpoints."""

    async def status(self) -> AdminStatusResponse:
        """Fetch a summary of the running Lyra service.

        Returns:
            API, storage, catalog, queue, and worker configuration status.

        """
        return await self._transport.get_admin_status()

    async def config_summary(self) -> ConfigSummaryResponse:
        """Fetch the effective non-secret service configuration.

        Returns:
            The API, queue, worker, job-store, and plugin path configuration.

        """
        return await self._transport.get_admin_config_summary()
