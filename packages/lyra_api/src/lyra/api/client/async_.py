"""Asynchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import ExitStack
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
from lyra.api.client.http import request_json
from lyra.api.client.polling import PollingState
from lyra.api.client.results import (
    dataframe_path,
    download_needs_text,
    require_file_result,
    successful_result,
    validate_download,
    validate_jsonl_format,
)
from lyra.api.exceptions import (
    DownloadError,
    ServiceUnavailableError,
)
from lyra.sdk.models.job import (
    FileJobResult,
    JobCancelResponse,
    JobCreateResponse,
    JobLifecycleStatus,
    JobListResponse,
    JobProgress,
    JobStatusInfo,
    ResultDescriptor,
    TableJobResult,
    TerminalJobResult,
)

if TYPE_CHECKING:
    import os
    from collections.abc import Awaitable, Callable
    from pathlib import Path
    from types import ModuleType

    import pandas as pd
    from lyra.api.client.endpoints import RequestSpec
    from lyra.api.options import RunOptions, SubmitOptions
    from lyra.sdk.models.admin import InstalledPluginListResponse, PluginRoutingResponse
    from lyra.sdk.models.data_types import DataTypesResponse
    from lyra.sdk.models.lookups import MetZoneCodeResponse
    from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfo
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


def _read_dataframe(pandas: ModuleType, path: Path, cleanup: ExitStack) -> pd.DataFrame:
    with cleanup:
        return pandas.read_json(path, lines=True)


def _consume_exception(future: asyncio.Future[_ResponseT]) -> None:
    if not future.cancelled():
        future.exception()


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
        poll_interval: float = 5.0,
        on_progress: Callable[[JobProgress], object] | None = None,
    ) -> Awaitable[_SuccessResultT]:
        """Poll immediately, then periodically, until a terminal result is available.

        Progress callbacks receive changed snapshots. Callback failures propagate.
        Local timeout, cancellation, or interruption never cancels the remote job.
        The monotonic deadline bounds network observations and scheduled waiting,
        including terminal-result retrieval. It cannot preempt arbitrary user
        callbacks or synchronous CPU processing. Zero expires immediately;
        None permits unlimited overall waiting with bounded individual requests.

        Returns:
            The successful result; failed or cancelled results raise MetricRunError.
        """
        return self._wait(
            wait_seconds=timeout, poll_interval=poll_interval, on_progress=on_progress
        )

    async def _wait(
        self,
        *,
        wait_seconds: float | None,
        poll_interval: float,
        on_progress: Callable[[JobProgress], object] | None,
    ) -> _SuccessResultT:
        state = PollingState(self.job_id, wait_seconds, poll_interval)
        terminal = False
        while True:
            try:
                request = (
                    endpoints.get_job_result(self.job_id)
                    if terminal
                    else endpoints.get_job(self.job_id)
                )
                response = await self._client.observe(
                    request, request_timeout=state.request_timeout(self._client.timeout)
                )
                state.remaining()
            except (DownloadError, ServiceUnavailableError) as exc:
                await asyncio.sleep(state.retry_delay(exc))
                continue
            state.failures = 0
            if terminal:
                return cast(
                    "_SuccessResultT",
                    successful_result(cast("TerminalJobResult", response)),
                )
            snapshot = cast("JobStatusInfo", response)
            if state.changed(snapshot.progress) and on_progress is not None:
                callback_result = on_progress(cast("JobProgress", snapshot.progress))
                if inspect.isawaitable(callback_result):
                    await callback_result
            terminal = snapshot.status in {"succeeded", "failed", "cancelled"}
            if not terminal:
                await asyncio.sleep(state.delay())


class _AsyncTransport(BaseTransport):  # ruff: ignore[too-many-public-methods] -- API surface
    """Private asynchronous HTTP implementation used by resource clients."""

    async def _request(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float | None = None
    ) -> _ResponseT:
        return await request_json(
            spec,
            self._http_url(spec.path),
            self._auth_headers if spec.authenticated else self.headers,
            self.timeout if request_timeout is None else request_timeout,
            exact_timeout=request_timeout is not None,
        )

    async def observe(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float
    ) -> _ResponseT:
        return await self._request(spec, request_timeout=request_timeout)

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

    async def list_plugins(self) -> InstalledPluginListResponse:
        return await self._request(endpoints.list_plugins())

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
        loop = asyncio.get_running_loop()
        pandas = await loop.run_in_executor(None, load_pandas)
        cleanup = ExitStack()
        try:
            temp_path = cleanup.enter_context(dataframe_path())
            await self.download_result(result_ref_or_job_id, temp_path, format="jsonl")
            parsing = loop.run_in_executor(
                None, _read_dataframe, pandas, temp_path, cleanup
            )
        except BaseException:
            cleanup.close()
            raise
        # Submission transfers cleanup to the worker, even if it has not started.
        parsing.add_done_callback(_consume_exception)
        return await asyncio.shield(parsing)

    async def get_data_types(self) -> DataTypesResponse:
        return await self._request(endpoints.get_data_types())

    async def get_metrics(self) -> MetricCatalogResponse:
        return await self._request(endpoints.get_metrics())

    async def get_metric(self, metric_name: str) -> MetricInfo:
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

    async def metric(self, name: str) -> MetricInfo:
        return await self._transport.get_metric(name)


class _JobsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def get(self, job_id: str) -> JobStatusInfo:
        return await self._transport.get_job(job_id)


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
        """Download and parse JSONL without blocking the event loop.

        Cancellation returns promptly. If parsing was submitted, its worker
        removes the temporary file after parsing finishes.

        Returns:
            The downloaded table as a pandas DataFrame.
        """
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
        return await handle.wait(
            timeout=wait_seconds,
            poll_interval=options.poll_interval if options else 5.0,
        )

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


class _AdminPluginsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> InstalledPluginListResponse:
        return await self._transport.list_plugins()


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
        jobs: Asynchronous job status endpoints.
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
        """Asynchronous job status endpoints."""
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
        plugins: Asynchronous installed plugin inspection endpoints.
        catalog: Asynchronous administrative catalog summary endpoints.
        workers: Asynchronous worker inspection endpoints.
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
        self.plugins = _AdminPluginsResource(transport)
        """Asynchronous installed plugin inspection endpoints."""
        self.catalog = _AdminCatalogResource(transport)
        """Asynchronous administrative catalog summary endpoints."""
        self.workers = _AdminWorkersResource(transport)
        """Asynchronous worker inspection endpoints."""
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
