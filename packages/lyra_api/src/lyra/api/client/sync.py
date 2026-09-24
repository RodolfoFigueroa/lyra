"""Synchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import time
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
    cast,
)

import requests
from lyra.api.client import endpoints
from lyra.api.client.base import BaseTransport, load_pandas
from lyra.api.client.endpoints import RequestSpec, validate_response
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
    from collections.abc import Callable

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


class JobHandle(Generic[_SuccessResultT]):
    """Observe a submitted job and retrieve its successful result synchronously.

    Instances are returned by `LyraClient.raw.submit`; applications normally
    do not construct handles directly.

    Attributes:
        submission: The response returned when the job was submitted.

    """

    def __init__(
        self,
        client: _SyncTransport,
        submission: JobCreateResponse,
    ) -> None:
        """Initialize a handle for an existing job submission."""
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

    def status(self) -> JobStatusInfo:
        """Fetch the job's current lifecycle status.

        Returns:
            The latest status reported by the API.

        """
        return self._client.get_job(self.job_id)

    def result(self) -> _SuccessResultT:
        """Fetch and return the job's successful terminal result.

        Returns:
            The table or file result associated with the job.

        Failed or cancelled jobs raise ``MetricRunError``.

        """
        result = self._client.get_job_result(self.job_id)
        return cast("_SuccessResultT", successful_result(result))

    def wait(
        self,
        *,
        timeout: float | None = None,
        poll_interval: float = 5.0,
        on_progress: Callable[[JobProgress], None] | None = None,
    ) -> _SuccessResultT:
        """Poll immediately, then periodically, until a terminal result is available.

        Progress callbacks receive changed snapshots. Callback failures propagate.
        A local timeout or cancellation never cancels the remote job.

        Returns:
            The successful result; failed or cancelled results raise MetricRunError.
        """
        state = PollingState(self.job_id, timeout, poll_interval)
        terminal = False
        while True:
            try:
                request = (
                    endpoints.get_job_result(self.job_id)
                    if terminal
                    else endpoints.get_job(self.job_id)
                )
                response = self._client.observe(
                    request, request_timeout=state.request_timeout(self._client.timeout)
                )
                state.remaining()
            except (DownloadError, ServiceUnavailableError) as exc:
                time.sleep(state.retry_delay(exc))
                continue
            state.failures = 0
            if terminal:
                return cast(
                    "_SuccessResultT",
                    successful_result(cast("TerminalJobResult", response)),
                )
            snapshot = cast("JobStatusInfo", response)
            if state.changed(snapshot.progress) and on_progress is not None:
                on_progress(cast("JobProgress", snapshot.progress))
            terminal = snapshot.status in {"succeeded", "failed", "cancelled"}
            if not terminal:
                time.sleep(state.delay())


class _SyncTransport(BaseTransport):  # ruff: ignore[too-many-public-methods] -- API surface
    """Private synchronous HTTP implementation used by resource clients."""

    def _request(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float | None = None
    ) -> _ResponseT:
        try:
            with requests.request(
                spec.method,
                self._http_url(spec.path),
                params=spec.params,
                json=spec.json_body,
                timeout=self.timeout if request_timeout is None else request_timeout,
                headers=self._auth_headers if spec.authenticated else self.headers,
            ) as response:
                return validate_response(
                    spec,
                    response.status_code,
                    response.text,
                    response.headers.get("content-type", ""),
                    response.headers.get("Retry-After"),
                )
        except requests.RequestException as exc:
            err = f"Failed to {spec.operation}: request error: {exc}"
            raise DownloadError(
                err,
                retryable=isinstance(exc, (requests.ConnectionError, requests.Timeout))
                and not isinstance(exc, requests.exceptions.SSLError),
            ) from exc

    def observe(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float
    ) -> _ResponseT:
        return self._request(spec, request_timeout=request_timeout)

    def get_liveness(self) -> LivenessResponse:
        return self._request(endpoints.get_liveness())

    def get_readiness(self) -> ReadinessResponse:
        return self._request(endpoints.get_readiness())

    def get_met_zone_code(self, name: str) -> MetZoneCodeResponse:
        return self._request(endpoints.get_met_zone_code(name))

    def create_job(
        self,
        metric: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> JobCreateResponse:
        return self._request(
            endpoints.create_job(metric, payload, idempotency_key=idempotency_key)
        )

    def submit_job(
        self,
        metric: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> JobHandle[SuccessfulJobResult]:
        return JobHandle(
            self,
            self.create_job(metric, payload, idempotency_key=idempotency_key),
        )

    def get_job(self, job_id: str) -> JobStatusInfo:
        return self._request(endpoints.get_job(job_id))

    def list_admin_jobs(
        self,
        *,
        limit: int = 50,
        status: JobLifecycleStatus | None = None,
        metric: str | None = None,
    ) -> JobListResponse:
        return self._request(
            endpoints.list_admin_jobs(limit=limit, status=status, metric=metric)
        )

    def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        return self._request(endpoints.cancel_admin_job(job_id))

    def list_plugin_repos(self) -> PluginRepoListResponse:
        return self._request(endpoints.list_plugin_repos())

    def list_plugin_routing(self) -> PluginRoutingResponse:
        return self._request(endpoints.list_plugin_routing())

    def get_admin_status(self) -> AdminStatusResponse:
        return self._request(endpoints.get_admin_status())

    def get_admin_config_summary(self) -> ConfigSummaryResponse:
        return self._request(endpoints.get_admin_config_summary())

    def get_admin_catalog(self) -> CatalogSummaryResponse:
        return self._request(endpoints.get_admin_catalog())

    def get_admin_workers(self) -> WorkersResponse:
        return self._request(endpoints.get_admin_workers())

    def get_admin_worker(self, worker_name: str) -> WorkerDetail:
        return self._request(endpoints.get_admin_worker(worker_name))

    def get_admin_queues(self) -> QueuesResponse:
        return self._request(endpoints.get_admin_queues())

    def get_job_result(self, job_id: str) -> TerminalJobResult:
        return self._request(endpoints.get_job_result(job_id))

    def _download(
        self,
        route: str,
        path: str | os.PathLike[str],
        *,
        operation: str,
        job_id: str | None = None,
    ) -> None:
        try:
            with requests.get(
                self._http_url(route),
                timeout=self.timeout,
                headers=self._auth_headers,
                stream=True,
            ) as response:
                if download_needs_text(
                    response.status_code,
                    response.headers.get("content-type", ""),
                    job_id,
                ):
                    validate_download(
                        response.status_code,
                        response.text,
                        response.headers.get("Retry-After"),
                        operation=operation,
                        job_id=job_id,
                    )
                with Path(path).open("wb") as file:
                    file.writelines(response.iter_content(chunk_size=65536))
        except requests.RequestException as exc:
            err = f"Failed to {operation}: request error: {exc}"
            raise DownloadError(err) from exc

    def download_job_result_to_file(
        self,
        job_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        self._download(
            f"jobs/{job_id}/result/download",
            path,
            operation="download job result",
            job_id=job_id,
        )

    def get_result_descriptor(self, result_ref_or_job_id: str) -> ResultDescriptor:
        return self._request(
            endpoints.get_result_descriptor(
                self._job_id_from_result_ref(result_ref_or_job_id)
            )
        )

    def download_result(
        self,
        result_ref_or_job_id: str,
        path: str | os.PathLike[str],
        *,
        format: str = "jsonl",  # ruff:ignore[builtin-argument-shadowing]
    ) -> None:
        validate_jsonl_format(format)
        job_id = self._job_id_from_result_ref(result_ref_or_job_id)
        self._download(
            f"jobs/{job_id}/result/table.jsonl", path, operation="download result"
        )

    def result_dataframe(self, result_ref_or_job_id: str) -> pd.DataFrame:
        pandas = load_pandas()
        with dataframe_path() as temp_path:
            self.download_result(result_ref_or_job_id, temp_path, format="jsonl")
            return pandas.read_json(temp_path, lines=True)

    def get_data_types(self) -> DataTypesResponse:
        return self._request(endpoints.get_data_types())

    def get_metrics(self) -> MetricCatalogResponse:
        return self._request(endpoints.get_metrics())

    def get_metric(self, metric_name: str) -> MetricInfoV4:
        return self._request(endpoints.get_metric(metric_name))


class _HealthResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def liveness(self) -> LivenessResponse:
        return self._transport.get_liveness()

    def readiness(self) -> ReadinessResponse:
        return self._transport.get_readiness()


class _LookupsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def met_zone_code(self, name: str) -> MetZoneCodeResponse:
        return self._transport.get_met_zone_code(name)


class _CatalogResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def data_types(self) -> DataTypesResponse:
        return self._transport.get_data_types()

    def metrics(self) -> MetricCatalogResponse:
        return self._transport.get_metrics()

    def metric(self, name: str) -> MetricInfoV4:
        return self._transport.get_metric(name)


class _JobsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def get(self, job_id: str) -> JobStatusInfo:
        return self._transport.get_job(job_id)


class _ResultsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def get(self, job_id: str) -> TerminalJobResult:
        return self._transport.get_job_result(job_id)

    def descriptor(self, ref: str) -> ResultDescriptor:
        return self._transport.get_result_descriptor(ref)

    def download(
        self,
        ref: str,
        path: str | os.PathLike[str],
        *,
        format: str = "jsonl",  # ruff:ignore[builtin-argument-shadowing]
    ) -> None:
        self._transport.download_result(ref, path, format=format)

    def download_file(self, job_id: str, path: str | os.PathLike[str]) -> None:
        self._transport.download_job_result_to_file(job_id, path)

    def dataframe(self, ref: str) -> pd.DataFrame:
        return self._transport.result_dataframe(ref)


class _RawMetricsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def create(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: SubmitOptions | None = None,
    ) -> JobCreateResponse:
        key = options.idempotency_key if options is not None else None
        return self._transport.create_job(metric, arguments, idempotency_key=key)

    def submit(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: SubmitOptions | None = None,
    ) -> JobHandle[SuccessfulJobResult]:
        key = options.idempotency_key if options is not None else None
        return self._transport.submit_job(metric, arguments, idempotency_key=key)

    def run(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: RunOptions | None = None,
    ) -> SuccessfulJobResult:
        key = options.idempotency_key if options is not None else None
        wait_seconds = options.timeout if options is not None else None
        handle = self._transport.submit_job(
            metric,
            arguments,
            idempotency_key=key,
        )
        return handle.wait(
            timeout=wait_seconds,
            poll_interval=options.poll_interval if options else 5.0,
        )

    def run_to_file(
        self,
        metric: str,
        arguments: JsonObject,
        path: str | os.PathLike[str],
        *,
        options: RunOptions | None = None,
    ) -> None:
        result = self.run(
            metric,
            arguments,
            options=options,
        )
        require_file_result(result)
        self._transport.download_job_result_to_file(result.job_id, path)


class _AdminJobsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(
        self,
        *,
        limit: int = 50,
        status: JobLifecycleStatus | None = None,
        metric: str | None = None,
    ) -> JobListResponse:
        return self._transport.list_admin_jobs(
            limit=limit,
            status=status,
            metric=metric,
        )

    def cancel(self, job_id: str) -> JobCancelResponse:
        return self._transport.cancel_admin_job(job_id)


class _AdminPluginReposResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> PluginRepoListResponse:
        return self._transport.list_plugin_repos()


class _AdminCatalogResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def summary(self) -> CatalogSummaryResponse:
        return self._transport.get_admin_catalog()


class _AdminWorkersResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> WorkersResponse:
        return self._transport.get_admin_workers()

    def get(self, name: str) -> WorkerDetail:
        return self._transport.get_admin_worker(name)


class _AdminQueuesResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> QueuesResponse:
        return self._transport.get_admin_queues()


class _AdminRoutingResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> PluginRoutingResponse:
        return self._transport.list_plugin_routing()


class LyraClient:
    """Access Lyra's consumer API with synchronous requests.

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
        health: Liveness and readiness endpoints.
        lookups: Public lookup endpoints.
        catalog: Metric and data-type discovery endpoints.
        jobs: Job status and event-stream endpoints.
        results: Job result inspection and download endpoints.
        raw: Untyped metric submission and execution endpoints.

    Example:
        >>> client = LyraClient("lyra.example.com", agent_api_key="...")
        >>> metrics = client.catalog.metrics()

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
        """Initialize a synchronous consumer client and its endpoint resources."""
        transport = _SyncTransport(
            host,
            timeout,
            headers,
            api_key=agent_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        """Liveness and readiness endpoints."""
        self.lookups = _LookupsResource(transport)
        """Public lookup endpoints."""
        self.catalog = _CatalogResource(transport)
        """Metric and data-type discovery endpoints."""
        self.jobs = _JobsResource(transport)
        """Job status and event-stream endpoints."""
        self.results = _ResultsResource(transport)
        """Job result inspection and download endpoints."""
        self.raw = _RawMetricsResource(transport)
        """Untyped metric submission and execution endpoints."""


class LyraAdminClient:
    """Access Lyra's administrator API with synchronous requests.

    Administrator credentials are intentionally isolated from `LyraClient`.
    This client exposes operational state and mutation endpoints, plus the public
    health checks, but does not expose consumer metric execution.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Default HTTP request timeout in seconds.
        headers: Additional headers included with every request.
        admin_api_key: Bearer token for administrator endpoints.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Liveness and readiness endpoints.
        jobs: Administrative job listing and cancellation endpoints.
        plugin_repos: Plugin repository configuration and synchronization endpoints.
        catalog: Administrative catalog summary and refresh endpoints.
        workers: Worker inspection and restart endpoints.
        queues: Queue inspection endpoints.
        routing: Metric-to-queue routing endpoints.

    Example:
        >>> admin = LyraAdminClient("lyra.example.com", admin_api_key="...")
        >>> status = admin.status()

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
        """Initialize a synchronous administrator client and its resources."""
        transport = _SyncTransport(
            host,
            timeout,
            headers,
            api_key=admin_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        """Liveness and readiness endpoints."""
        self.jobs = _AdminJobsResource(transport)
        """Administrative job listing and cancellation endpoints."""
        self.plugin_repos = _AdminPluginReposResource(transport)
        """Plugin repository configuration and synchronization endpoints."""
        self.catalog = _AdminCatalogResource(transport)
        """Administrative catalog summary and refresh endpoints."""
        self.workers = _AdminWorkersResource(transport)
        """Worker inspection and restart endpoints."""
        self.queues = _AdminQueuesResource(transport)
        """Queue inspection endpoints."""
        self.routing = _AdminRoutingResource(transport)
        """Metric-to-queue routing endpoints."""

    def status(self) -> AdminStatusResponse:
        """Fetch a summary of the running Lyra service.

        Returns:
            API, storage, catalog, queue, and worker configuration status.

        """
        return self._transport.get_admin_status()

    def config_summary(self) -> ConfigSummaryResponse:
        """Fetch the effective non-secret service configuration.

        Returns:
            The API, queue, worker, job-store, and plugin path configuration.

        """
        return self._transport.get_admin_config_summary()
