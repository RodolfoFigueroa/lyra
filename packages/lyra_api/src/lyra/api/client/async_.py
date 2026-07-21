"""Asynchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import asyncio
import inspect
import json
import random
import tempfile
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    NotRequired,
    TypedDict,
    TypeVar,
    Unpack,
    cast,
)

import aiofiles
import aiofiles.os
import aiohttp
from lyra.api.client.base import BaseTransport, load_pandas, service_unavailable_error
from lyra.api.exceptions import (
    DownloadError,
    JobEventCursorGapError,
    JobEventStreamError,
    JobWaitTimeoutError,
    MetricRunError,
)
from lyra.sdk.models import (
    AdminStatusResponse,
    CancelledJobResult,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    CreatePluginRepoRequest,
    CreatePluginRepoResponse,
    DataTypesResponse,
    DeleteMetricQueueResponse,
    DeletePluginRepoResponse,
    FailedJobResult,
    FileJobResult,
    JobCancelResponse,
    JobCreateResponse,
    JobEventRecord,
    JobLifecycleEvent,
    JobLifecycleStatus,
    JobListResponse,
    JobMessageEvent,
    JobProgressEvent,
    JobStatusInfo,
    LivenessResponse,
    MetricQueueAssignmentResponse,
    MetZoneCodeResponse,
    PluginCatalogRefreshResponse,
    PluginRepoListResponse,
    PluginRoutingResponse,
    QueuesResponse,
    ReadinessResponse,
    ResultDescriptor,
    SetMetricQueueRequest,
    SyncPluginRepoResponse,
    TableJobResult,
    TerminalJobResult,
    UpdatePluginRepoRequest,
    UpdatePluginRepoResponse,
    WorkerDetail,
    WorkerRestartResponse,
    WorkersResponse,
    parse_job_result,
)
from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfoV4
from pydantic import BaseModel

if TYPE_CHECKING:
    import os
    from collections.abc import AsyncIterator, Awaitable, Callable

    import pandas as pd
    from lyra.api.options import RunOptions, SubmitOptions
    from lyra.sdk.types import JsonObject

TERMINAL_EVENTS = {"succeeded", "failed", "cancelled"}
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_SuccessResultT = TypeVar("_SuccessResultT", bound=TableJobResult | FileJobResult)
SuccessfulJobResult = TableJobResult | FileJobResult


@dataclass
class _SSEEventBuffer:
    data_lines: list[str] = field(default_factory=list)
    event_id: str | None = None

    def flush(self) -> JobEventRecord | None:
        if not self.data_lines:
            return None
        if self.event_id is None:
            err = "Job event did not include an SSE id."
            raise DownloadError(err)
        record = JobEventRecord(
            id=self.event_id,
            event=json.loads("\n".join(self.data_lines)),
        )
        self.data_lines.clear()
        self.event_id = None
        return record

    def add(self, line: str) -> JobEventRecord | None:
        if not line:
            return self.flush()
        if line.startswith(":"):
            return None
        field_name, separator, value = line.partition(":")
        if not separator:
            return None
        value = value.removeprefix(" ")
        if field_name == "data":
            self.data_lines.append(value)
        elif field_name == "id":
            self.event_id = value
        return None


async def _aiter_sse_job_events(
    lines: AsyncIterator[str],
    *,
    on_line: Callable[[], None] | None = None,
) -> AsyncIterator[JobEventRecord]:
    buffer = _SSEEventBuffer()
    async for line in lines:
        if on_line is not None:
            on_line()
        record = buffer.add(line)
        if record is not None:
            yield record
    record = buffer.flush()
    if record is not None:
        yield record


def _terminal_event(record: JobEventRecord) -> bool:
    event = record.event
    return isinstance(event, JobLifecycleEvent) and event.status in TERMINAL_EVENTS


def _event_wait_deadline(wait_seconds: float | None) -> float | None:
    return None if wait_seconds is None else time.monotonic() + wait_seconds


def _check_event_deadline(
    deadline: float | None,
    *,
    job_id: str,
    cursor: str | None,
    attempts: int,
) -> None:
    if deadline is None or time.monotonic() < deadline:
        return
    err = f"Timed out waiting for events from job {job_id}."
    raise JobWaitTimeoutError(
        err,
        job_id=job_id,
        last_event_id=cursor,
        attempts=attempts,
    )


@dataclass
class _EventDeadlineGuard:
    deadline: float | None
    job_id: str
    cursor: str | None
    attempts: int

    def __call__(self) -> None:
        _check_event_deadline(
            self.deadline,
            job_id=self.job_id,
            cursor=self.cursor,
            attempts=self.attempts,
        )


def _event_retry_delay(
    deadline: float | None,
    attempts: int,
    jitter: random.Random,
) -> float:
    cap = min(8.0, 0.5 * (2 ** (attempts - 1)))
    delay = jitter.uniform(0, cap)
    if deadline is None:
        return delay
    return min(delay, max(0.0, deadline - time.monotonic()))


def _event_read_timeout(deadline: float | None, default: float) -> float:
    if deadline is None:
        return default
    return max(0.001, min(default, deadline - time.monotonic()))


def _validate_max_reconnect_attempts(max_reconnect_attempts: int) -> None:
    if max_reconnect_attempts < 0:
        err = "max_reconnect_attempts must be non-negative"
        raise ValueError(err)


async def _validate_event_response(
    response: aiohttp.ClientResponse,
    *,
    job_id: str,
    cursor: str | None,
    attempts: int,
) -> None:
    if response.status == 409:
        err = f"Event history for job {job_id} no longer contains {cursor}."
        raise JobEventCursorGapError(
            err,
            job_id=job_id,
            last_event_id=cursor,
            attempts=attempts,
        )
    if response.status == 200:
        return
    if response.status >= 500:
        response.raise_for_status()
    text = await response.text()
    err = f"Failed to stream job events. HTTP {response.status}: {text}"
    raise DownloadError(err)


class AsyncJobHandle(Generic[_SuccessResultT]):
    """Observe a submitted job and retrieve its successful result asynchronously.

    Instances are returned by :meth:`AsyncLyraClient.raw.submit`; applications
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

        Raises:
            MetricRunError: If the job failed or was cancelled.

        """
        result = await self._client.get_job_result(self.job_id)
        if isinstance(result, FailedJobResult | CancelledJobResult):
            raise MetricRunError(result)
        return cast("_SuccessResultT", result)

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
            callbacks: list[object] = []
            if on_event is not None:
                callbacks.append(on_event(record))
            if isinstance(record.event, JobProgressEvent) and on_progress is not None:
                callbacks.append(on_progress(record.event))
            if isinstance(record.event, JobMessageEvent) and on_message is not None:
                callbacks.append(on_message(record.event))
            for callback_result in callbacks:
                if inspect.isawaitable(callback_result):
                    await callback_result
            if _terminal_event(record):
                return await self.result()
        err = f"Job {self.job_id} event stream ended before a terminal event."
        raise JobEventStreamError(
            err,
            job_id=self.job_id,
            last_event_id=None,
            attempts=0,
        )


async def _response_lines(response: aiohttp.ClientResponse) -> AsyncIterator[str]:
    async for raw_line in response.content:
        yield raw_line.decode().rstrip("\r\n")


class _RequestModelOptions(TypedDict):
    error_context: str
    authenticated: NotRequired[bool]
    expected_status: NotRequired[int]
    params: NotRequired[dict[str, Any] | None]
    json_body: NotRequired[dict[str, Any] | None]


@asynccontextmanager
async def _translate_client_errors(message: str) -> AsyncIterator[None]:
    try:
        yield
    except aiohttp.ClientError as exc:
        err = f"{message}: {exc}"
        raise DownloadError(err) from exc


class _AsyncTransport(BaseTransport):  # ruff: ignore[too-many-public-methods] -- API surface
    """Private asynchronous HTTP implementation used by resource clients."""

    async def _request_model(
        self,
        method: str,
        path: str,
        response_model: type[_ModelT],
        **options: Unpack[_RequestModelOptions],
    ) -> _ModelT:
        error_context = options["error_context"]
        authenticated = options.get("authenticated", True)
        expected_status = options.get("expected_status", 200)
        params = options.get("params")
        json_body = options.get("json_body")
        async with _translate_client_errors(f"{error_context} request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.request(
                    method,
                    self._http_url(path),
                    params=params,
                    json=json_body,
                    headers=self._auth_headers if authenticated else self.headers,
                ) as response,
            ):
                if response.status != expected_status:
                    if response.status == 503:
                        unavailable = service_unavailable_error(
                            await response.json(),
                            response.headers.get("Retry-After"),
                        )
                        if unavailable is not None:
                            raise unavailable
                    text = await response.text()
                    err = f"Failed to {error_context}. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return response_model.model_validate(await response.json())

    async def get_liveness(self) -> LivenessResponse:
        async with _translate_client_errors("Liveness request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("live"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to fetch liveness. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return LivenessResponse.model_validate(await response.json())

    async def get_readiness(self) -> ReadinessResponse:
        async with _translate_client_errors("Readiness request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("ready"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status not in {200, 503}:
                    text = await response.text()
                    err = f"Failed to fetch readiness. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return ReadinessResponse.model_validate(await response.json())

    async def get_met_zone_code(self, name: str) -> MetZoneCodeResponse:
        return await self._request_model(
            "GET",
            "lookups/met-zones",
            MetZoneCodeResponse,
            error_context="fetch met-zone lookup",
            authenticated=False,
            params={"name": name},
        )

    async def create_job(
        self,
        metric: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> JobCreateResponse:
        body: dict[str, Any] = {"metric": metric, "input": payload}
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key

        async with _translate_client_errors("Job creation error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._http_url("jobs"),
                    json=body,
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 202:
                    text = await response.text()
                    err = f"Failed to create job. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return JobCreateResponse.model_validate(await response.json())

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
        async with _translate_client_errors("Job status error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to fetch job. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return JobStatusInfo.model_validate(await response.json())

    async def list_admin_jobs(
        self,
        *,
        limit: int = 50,
        status: JobLifecycleStatus | None = None,
        metric: str | None = None,
    ) -> JobListResponse:
        params: dict[str, int | str] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if metric is not None:
            params["metric"] = metric
        async with _translate_client_errors("Admin job list error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/jobs"),
                    params=params,
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to list admin jobs. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return JobListResponse.model_validate(await response.json())

    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        async with _translate_client_errors("Admin job cancellation error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._http_url(f"admin/jobs/{job_id}/cancel"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to cancel admin job. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return JobCancelResponse.model_validate(await response.json())

    async def list_plugin_repos(self) -> PluginRepoListResponse:
        return await self._request_model(
            "GET",
            "admin/plugin-repos",
            PluginRepoListResponse,
            error_context="list plugin repos",
        )

    async def create_plugin_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse:
        request = CreatePluginRepoRequest(
            source=source,
            id=repo_id,
            enabled=enabled,
        )
        return await self._request_model(
            "POST",
            "admin/plugin-repos",
            CreatePluginRepoResponse,
            error_context="create plugin repo",
            json_body=request.model_dump(mode="json", exclude_none=True),
        )

    async def update_plugin_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> UpdatePluginRepoResponse:
        request = UpdatePluginRepoRequest(source=source, enabled=enabled)
        return await self._request_model(
            "PATCH",
            f"admin/plugin-repos/{repo_id}",
            UpdatePluginRepoResponse,
            error_context="update plugin repo",
            json_body=request.model_dump(mode="json", exclude_none=True),
        )

    async def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse:
        return await self._request_model(
            "DELETE",
            f"admin/plugin-repos/{repo_id}",
            DeletePluginRepoResponse,
            error_context="delete plugin repo",
        )

    async def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse:
        return await self._request_model(
            "POST",
            f"admin/plugin-repos/{repo_id}/sync",
            SyncPluginRepoResponse,
            error_context="sync plugin repo",
        )

    async def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse:
        return await self._request_model(
            "POST",
            "admin/plugin-catalog/refresh",
            PluginCatalogRefreshResponse,
            error_context="refresh plugin catalog",
        )

    async def restart_workers(
        self,
        *,
        timeout: float = 30.0,  # ruff:ignore[async-function-with-timeout]
    ) -> WorkerRestartResponse:
        return await self._request_model(
            "POST",
            "admin/workers/restart",
            WorkerRestartResponse,
            error_context="restart workers",
            params={"timeout": timeout},
        )

    async def list_plugin_routing(self) -> PluginRoutingResponse:
        return await self._request_model(
            "GET",
            "admin/plugin-routing",
            PluginRoutingResponse,
            error_context="list plugin routing",
        )

    async def set_plugin_routing(
        self,
        metric_name: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse:
        request = SetMetricQueueRequest(queue=queue)
        return await self._request_model(
            "PUT",
            f"admin/plugin-routing/{metric_name}",
            MetricQueueAssignmentResponse,
            error_context="set plugin routing",
            json_body=request.model_dump(mode="json"),
        )

    async def delete_plugin_routing(
        self,
        metric_name: str,
    ) -> DeleteMetricQueueResponse:
        return await self._request_model(
            "DELETE",
            f"admin/plugin-routing/{metric_name}",
            DeleteMetricQueueResponse,
            error_context="delete plugin routing",
        )

    async def get_admin_status(self) -> AdminStatusResponse:
        async with _translate_client_errors("Admin status request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/status"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin status. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return AdminStatusResponse.model_validate(await response.json())

    async def get_admin_config_summary(self) -> ConfigSummaryResponse:
        async with _translate_client_errors("Admin config summary request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/config-summary"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        "Failed to fetch admin config summary. "
                        f"HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return ConfigSummaryResponse.model_validate(await response.json())

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        async with _translate_client_errors("Admin catalog request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/catalog"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin catalog. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return CatalogSummaryResponse.model_validate(await response.json())

    async def get_admin_workers(self) -> WorkersResponse:
        async with _translate_client_errors("Admin workers request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/workers"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin workers. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return WorkersResponse.model_validate(await response.json())

    async def get_admin_worker(self, worker_name: str) -> WorkerDetail:
        async with _translate_client_errors("Admin worker request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"admin/workers/{worker_name}"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin worker. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return WorkerDetail.model_validate(await response.json())

    async def get_admin_queues(self) -> QueuesResponse:
        async with _translate_client_errors("Admin queues request error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/queues"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin queues. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return QueuesResponse.model_validate(await response.json())

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
        last_event_id: str | None,
        kinds: set[str] | None,
        wait_seconds: float | None,
        max_reconnect_attempts: int,
    ) -> AsyncIterator[JobEventRecord]:
        _validate_max_reconnect_attempts(max_reconnect_attempts)
        deadline = _event_wait_deadline(wait_seconds)
        cursor = last_event_id
        attempts = 0
        jitter = random.SystemRandom()
        while True:
            _check_event_deadline(
                deadline,
                job_id=job_id,
                cursor=cursor,
                attempts=attempts,
            )
            headers = dict(self._auth_headers)
            if cursor is not None:
                headers["Last-Event-ID"] = cursor
            try:  # ruff: ignore[too-many-statements-in-try-clause] -- live stream
                stream_timeout = aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=self.timeout,
                    sock_read=_event_read_timeout(deadline, self.timeout),
                )
                async with (
                    aiohttp.ClientSession(timeout=stream_timeout) as session,
                    session.get(
                        self._http_url(f"jobs/{job_id}/events"),
                        headers=headers,
                    ) as response,
                ):
                    await _validate_event_response(
                        response,
                        job_id=job_id,
                        cursor=cursor,
                        attempts=attempts,
                    )
                    deadline_guard = _EventDeadlineGuard(
                        deadline=deadline,
                        job_id=job_id,
                        cursor=cursor,
                        attempts=attempts,
                    )
                    async for record in _aiter_sse_job_events(
                        _response_lines(response),
                        on_line=deadline_guard,
                    ):
                        if record.id == cursor:
                            continue
                        cursor = record.id
                        attempts = 0
                        deadline_guard.cursor = cursor
                        deadline_guard.attempts = attempts
                        terminal = _terminal_event(record)
                        if kinds is None or record.event.kind in kinds:
                            yield record  # ruff: ignore[yield-in-context-manager-in-async-generator] -- live response
                        if terminal:
                            return
            except JobEventCursorGapError:
                raise
            except aiohttp.ClientError:
                pass

            attempts += 1
            if attempts > max_reconnect_attempts:
                err = f"Could not resume the event stream for job {job_id}."
                raise JobEventStreamError(
                    err,
                    job_id=job_id,
                    last_event_id=cursor,
                    attempts=attempts,
                )
            await asyncio.sleep(_event_retry_delay(deadline, attempts, jitter))

    async def get_job_result(self, job_id: str) -> TerminalJobResult:
        async with _translate_client_errors("Job result error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}/result"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to fetch job result. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                if "application/json" not in response.headers.get("content-type", ""):
                    err = "Job result response was not JSON."
                    raise DownloadError(err)
                return parse_job_result(await response.json())

    async def download_job_result_to_file(
        self,
        job_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        output_path = Path(path)
        async with _translate_client_errors("Job result download error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}/result/download"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to download job result. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)

                if "application/json" in response.headers.get("content-type", ""):
                    result = parse_job_result(await response.json())
                    err = (
                        f"Job {job_id} returned {result.status} JSON result, "
                        "not a file."
                    )
                    raise DownloadError(err)

                async with aiofiles.open(output_path, "wb") as file:
                    async for chunk in response.content.iter_chunked(65536):
                        await file.write(chunk)

    async def get_result_descriptor(
        self,
        result_ref_or_job_id: str,
    ) -> ResultDescriptor:
        job_id = self._job_id_from_result_ref(result_ref_or_job_id)
        return await self._request_model(
            "GET",
            f"jobs/{job_id}/result/descriptor",
            ResultDescriptor,
            error_context="fetch result descriptor",
        )

    async def download_result(
        self,
        result_ref_or_job_id: str,
        path: str | os.PathLike[str],
        *,
        format: str = "jsonl",  # ruff:ignore[builtin-argument-shadowing]
    ) -> None:
        if format != "jsonl":
            err = "Only JSONL result downloads are supported. Use format='jsonl'."
            raise DownloadError(err)

        job_id = self._job_id_from_result_ref(result_ref_or_job_id)
        output_path = Path(path)
        async with _translate_client_errors("Result download error"):
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}/result/table.jsonl"),
                    headers=self._auth_headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to download result. HTTP {response.status}: {text}"
                    raise DownloadError(err)

                async with aiofiles.open(output_path, "wb") as file:
                    async for chunk in response.content.iter_chunked(65536):
                        await file.write(chunk)

    async def result_dataframe(self, result_ref_or_job_id: str) -> pd.DataFrame:
        pandas = load_pandas()
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        try:
            await self.download_result(result_ref_or_job_id, temp_path, format="jsonl")
            return pandas.read_json(temp_path, lines=True)
        finally:
            with suppress(FileNotFoundError):
                await aiofiles.os.remove(temp_path)

    async def get_data_types(self) -> DataTypesResponse:
        data_types_url = self._http_url("data-types")
        data_types: Any = None
        status: int = 0

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    data_types_url,
                    headers=self.headers,
                ) as response,
            ):
                status = response.status
                if status == 200:
                    data_types = await response.json()
        except aiohttp.ClientError as exc:
            err = f"Data types request error: {exc}"
            raise DownloadError(err) from exc

        if status != 200:
            err = f"Failed to fetch data types. HTTP {status}"
            raise DownloadError(err)

        try:
            return DataTypesResponse.model_validate(data_types)
        except ValueError as exc:
            err = "Invalid data types response format"
            raise DownloadError(err) from exc

    async def get_metrics(self) -> MetricCatalogResponse:
        metrics: Any = None
        status: int = 0

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("metrics"),
                    headers=self.headers,
                ) as response,
            ):
                status = response.status
                if status == 200:
                    metrics = await response.json()
        except aiohttp.ClientError as exc:
            err = f"Metrics request error: {exc}"
            raise DownloadError(err) from exc

        if status != 200:
            err = f"Failed to fetch metrics. HTTP {status}"
            raise DownloadError(err)

        return MetricCatalogResponse.model_validate(metrics)

    async def get_metric(self, metric_name: str) -> MetricInfoV4:
        metric: Any = None
        status: int = 0

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"metrics/{metric_name}"),
                    headers=self.headers,
                ) as response,
            ):
                status = response.status
                if status == 200:
                    metric = await response.json()
        except aiohttp.ClientError as exc:
            err = f"Metric request error: {exc}"
            raise DownloadError(err) from exc

        if status != 200:
            err = f"Failed to fetch metric. HTTP {status}"
            raise DownloadError(err)

        return MetricInfoV4.model_validate(metric)


class _AsyncAdminTransport(_AsyncTransport):
    """Asynchronous transport configured exclusively with an administrator key."""


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
        if not isinstance(result, FileJobResult):
            err = f"Job {result.job_id} did not produce a file result."
            raise DownloadError(err)
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

    async def create(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse:
        return await self._transport.create_plugin_repo(
            source,
            repo_id=repo_id,
            enabled=enabled,
        )

    async def update(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> UpdatePluginRepoResponse:
        return await self._transport.update_plugin_repo(
            repo_id,
            source=source,
            enabled=enabled,
        )

    async def delete(self, repo_id: str) -> DeletePluginRepoResponse:
        return await self._transport.delete_plugin_repo(repo_id)

    async def sync(self, repo_id: str) -> SyncPluginRepoResponse:
        return await self._transport.sync_plugin_repo(repo_id)


class _AdminCatalogResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def summary(self) -> CatalogSummaryResponse:
        return await self._transport.get_admin_catalog()

    async def refresh(self) -> PluginCatalogRefreshResponse:
        return await self._transport.refresh_plugin_catalog()


class _WorkerRestartOptions(TypedDict):
    timeout: NotRequired[float]


class _AdminWorkersResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> WorkersResponse:
        return await self._transport.get_admin_workers()

    async def get(self, name: str) -> WorkerDetail:
        return await self._transport.get_admin_worker(name)

    async def restart(
        self,
        **options: Unpack[_WorkerRestartOptions],
    ) -> WorkerRestartResponse:
        return await self._transport.restart_workers(
            timeout=options.get("timeout", 30.0)
        )


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

    async def set(
        self,
        metric: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse:
        return await self._transport.set_plugin_routing(metric, queue)

    async def delete(self, metric: str) -> DeleteMetricQueueResponse:
        return await self._transport.delete_plugin_routing(metric)


class AsyncLyraClient:
    """Access Lyra's consumer API with asynchronous requests.

    The client groups endpoints into resource namespaces. Use :attr:`catalog` to
    discover metrics, :attr:`raw` to submit or run metrics without a generated
    typed client, and :attr:`jobs` and :attr:`results` to observe existing jobs.

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
    :class:`AsyncLyraClient`. This client exposes operational state and mutation
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
        transport = _AsyncAdminTransport(
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
