from __future__ import annotations

import json
import random
import tempfile
import time
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

import requests
from lyra.api.client.base import _BaseTransport, _load_pandas, service_unavailable_error
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
    from collections.abc import Callable, Iterable, Iterator

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
        if line == "":
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


def _iter_sse_job_events(
    lines: Iterable[str | bytes],
    *,
    on_line: Callable[[], None] | None = None,
) -> Iterator[JobEventRecord]:
    buffer = _SSEEventBuffer()
    for line in lines:
        if on_line is not None:
            on_line()
        decoded_line = line.decode() if isinstance(line, bytes) else line
        record = buffer.add(decoded_line)
        if record is not None:
            yield record
    record = buffer.flush()
    if record is not None:
        yield record


def _terminal_event(record: JobEventRecord) -> bool:
    event = record.event
    return isinstance(event, JobLifecycleEvent) and event.status in TERMINAL_EVENTS


def _validate_event_response(
    response: requests.Response,
    *,
    job_id: str,
    cursor: str | None,
    attempts: int,
) -> None:
    if response.status_code == 409:
        err = f"Event history for job {job_id} no longer contains {cursor}."
        raise JobEventCursorGapError(
            err,
            job_id=job_id,
            last_event_id=cursor,
            attempts=attempts,
        )
    if response.status_code == 200:
        return
    if response.status_code >= 500:
        response.raise_for_status()
    err = f"Failed to stream job events. HTTP {response.status_code}: {response.text}"
    raise DownloadError(err)


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


class JobHandle(Generic[_SuccessResultT]):
    """Synchronous observation and result handle for one submitted job."""

    def __init__(
        self,
        client: _SyncTransport,
        submission: JobCreateResponse,
    ) -> None:
        self._client = client
        self.submission = submission

    @property
    def job_id(self) -> str:
        return self.submission.job_id

    @property
    def metric(self) -> str:
        return self.submission.metric

    def status(self) -> JobStatusInfo:
        return self._client.get_job(self.job_id)

    def events(
        self,
        *,
        after_id: str | None = None,
        kinds: set[str] | None = None,
        timeout: float | None = None,
        max_reconnect_attempts: int = 5,
    ) -> Iterator[JobEventRecord]:
        return self._client.iter_job_events(
            self.job_id,
            last_event_id=after_id,
            kinds=kinds,
            timeout=timeout,
            max_reconnect_attempts=max_reconnect_attempts,
        )

    def result(self) -> _SuccessResultT:
        result = self._client.get_job_result(self.job_id)
        if isinstance(result, FailedJobResult | CancelledJobResult):
            raise MetricRunError(result)
        return cast("_SuccessResultT", result)

    def wait(
        self,
        *,
        timeout: float | None = None,
        on_event: Callable[[JobEventRecord], None] | None = None,
        on_progress: Callable[[JobProgressEvent], None] | None = None,
        on_message: Callable[[JobMessageEvent], None] | None = None,
    ) -> _SuccessResultT:
        for record in self.events(timeout=timeout):
            if on_event is not None:
                on_event(record)
            if isinstance(record.event, JobProgressEvent) and on_progress is not None:
                on_progress(record.event)
            if isinstance(record.event, JobMessageEvent) and on_message is not None:
                on_message(record.event)
            if _terminal_event(record):
                return self.result()
        err = f"Job {self.job_id} event stream ended before a terminal event."
        raise JobEventStreamError(
            err,
            job_id=self.job_id,
            last_event_id=None,
            attempts=0,
        )


class _RequestModelOptions(TypedDict):
    error_context: str
    authenticated: NotRequired[bool]
    expected_status: NotRequired[int]
    params: NotRequired[dict[str, Any] | None]
    json_body: NotRequired[dict[str, Any] | None]


class _SyncTransport(_BaseTransport):
    """Private synchronous HTTP implementation used by resource clients."""

    def _request_model(
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
        try:
            response = requests.request(
                method,
                self._http_url(path),
                params=params,
                json=json_body,
                timeout=self.timeout,
                headers=self._auth_headers if authenticated else self.headers,
            )
        except requests.RequestException as exc:
            err = f"{error_context} request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != expected_status:
            if response.status_code == 503:
                unavailable = service_unavailable_error(
                    response.json(),
                    response.headers.get("Retry-After"),
                )
                if unavailable is not None:
                    raise unavailable
            err = (
                f"Failed to {error_context}. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        return response_model.model_validate(response.json())

    def get_liveness(self) -> LivenessResponse:
        try:
            response = requests.get(
                self._http_url("live"),
                timeout=self.timeout,
                headers=self.headers,
            )
        except requests.RequestException as exc:
            err = f"Liveness request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                "Failed to fetch liveness. "
                f"HTTP {response.status_code}: {response.text}"
            )
            raise DownloadError(err)
        return LivenessResponse.model_validate(response.json())

    def get_readiness(self) -> ReadinessResponse:
        try:
            response = requests.get(
                self._http_url("ready"),
                timeout=self.timeout,
                headers=self.headers,
            )
        except requests.RequestException as exc:
            err = f"Readiness request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code not in {200, 503}:
            err = (
                "Failed to fetch readiness. "
                f"HTTP {response.status_code}: {response.text}"
            )
            raise DownloadError(err)
        return ReadinessResponse.model_validate(response.json())

    def get_met_zone_code(self, name: str) -> MetZoneCodeResponse:
        return self._request_model(
            "GET",
            "lookups/met-zones",
            MetZoneCodeResponse,
            error_context="fetch met-zone lookup",
            authenticated=False,
            params={"name": name},
        )

    def create_job(
        self,
        metric: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> JobCreateResponse:
        body: dict[str, Any] = {"metric": metric, "input": payload}
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key

        try:
            response = requests.post(
                self._http_url("jobs"),
                json=body,
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Job creation error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 202:
            err = f"Failed to create job. HTTP {response.status_code}: {response.text}"
            raise DownloadError(err)
        return JobCreateResponse.model_validate(response.json())

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
        try:
            response = requests.get(
                self._http_url(f"jobs/{job_id}"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Job status error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = f"Failed to fetch job. HTTP {response.status_code}: {response.text}"
            raise DownloadError(err)
        return JobStatusInfo.model_validate(response.json())

    def list_admin_jobs(
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
        try:
            response = requests.get(
                self._http_url("admin/jobs"),
                params=params,
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Admin job list error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                f"Failed to list admin jobs. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        return JobListResponse.model_validate(response.json())

    def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        try:
            response = requests.post(
                self._http_url(f"admin/jobs/{job_id}/cancel"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Admin job cancellation error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                f"Failed to cancel admin job. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        return JobCancelResponse.model_validate(response.json())

    def list_plugin_repos(self) -> PluginRepoListResponse:
        return self._request_model(
            "GET",
            "admin/plugin-repos",
            PluginRepoListResponse,
            error_context="list plugin repos",
        )

    def create_plugin_repo(
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
        return self._request_model(
            "POST",
            "admin/plugin-repos",
            CreatePluginRepoResponse,
            error_context="create plugin repo",
            json_body=request.model_dump(mode="json", exclude_none=True),
        )

    def update_plugin_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> UpdatePluginRepoResponse:
        request = UpdatePluginRepoRequest(source=source, enabled=enabled)
        return self._request_model(
            "PATCH",
            f"admin/plugin-repos/{repo_id}",
            UpdatePluginRepoResponse,
            error_context="update plugin repo",
            json_body=request.model_dump(mode="json", exclude_none=True),
        )

    def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse:
        return self._request_model(
            "DELETE",
            f"admin/plugin-repos/{repo_id}",
            DeletePluginRepoResponse,
            error_context="delete plugin repo",
        )

    def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse:
        return self._request_model(
            "POST",
            f"admin/plugin-repos/{repo_id}/sync",
            SyncPluginRepoResponse,
            error_context="sync plugin repo",
        )

    def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse:
        return self._request_model(
            "POST",
            "admin/plugin-catalog/refresh",
            PluginCatalogRefreshResponse,
            error_context="refresh plugin catalog",
        )

    def restart_workers(self, *, timeout: float = 30.0) -> WorkerRestartResponse:
        return self._request_model(
            "POST",
            "admin/workers/restart",
            WorkerRestartResponse,
            error_context="restart workers",
            params={"timeout": timeout},
        )

    def list_plugin_routing(self) -> PluginRoutingResponse:
        return self._request_model(
            "GET",
            "admin/plugin-routing",
            PluginRoutingResponse,
            error_context="list plugin routing",
        )

    def set_plugin_routing(
        self,
        metric_name: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse:
        request = SetMetricQueueRequest(queue=queue)
        return self._request_model(
            "PUT",
            f"admin/plugin-routing/{metric_name}",
            MetricQueueAssignmentResponse,
            error_context="set plugin routing",
            json_body=request.model_dump(mode="json"),
        )

    def delete_plugin_routing(self, metric_name: str) -> DeleteMetricQueueResponse:
        return self._request_model(
            "DELETE",
            f"admin/plugin-routing/{metric_name}",
            DeleteMetricQueueResponse,
            error_context="delete plugin routing",
        )

    def get_admin_status(self) -> AdminStatusResponse:
        try:
            response = requests.get(
                self._http_url("admin/status"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Admin status request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                f"Failed to fetch admin status. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        return AdminStatusResponse.model_validate(response.json())

    def get_admin_config_summary(self) -> ConfigSummaryResponse:
        try:
            response = requests.get(
                self._http_url("admin/config-summary"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Admin config summary request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                "Failed to fetch admin config summary. "
                f"HTTP {response.status_code}: {response.text}"
            )
            raise DownloadError(err)
        return ConfigSummaryResponse.model_validate(response.json())

    def get_admin_catalog(self) -> CatalogSummaryResponse:
        try:
            response = requests.get(
                self._http_url("admin/catalog"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Admin catalog request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                f"Failed to fetch admin catalog. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        return CatalogSummaryResponse.model_validate(response.json())

    def get_admin_workers(self) -> WorkersResponse:
        try:
            response = requests.get(
                self._http_url("admin/workers"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Admin workers request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                f"Failed to fetch admin workers. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        return WorkersResponse.model_validate(response.json())

    def get_admin_worker(self, worker_name: str) -> WorkerDetail:
        try:
            response = requests.get(
                self._http_url(f"admin/workers/{worker_name}"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Admin worker request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                f"Failed to fetch admin worker. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        return WorkerDetail.model_validate(response.json())

    def get_admin_queues(self) -> QueuesResponse:
        try:
            response = requests.get(
                self._http_url("admin/queues"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Admin queues request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                f"Failed to fetch admin queues. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        return QueuesResponse.model_validate(response.json())

    def iter_job_events(
        self,
        job_id: str,
        *,
        last_event_id: str | None = None,
        kinds: set[str] | None = None,
        timeout: float | None = None,
        max_reconnect_attempts: int = 5,
    ) -> Iterator[JobEventRecord]:
        _validate_max_reconnect_attempts(max_reconnect_attempts)
        deadline = _event_wait_deadline(timeout)
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
            try:
                with requests.get(
                    self._http_url(f"jobs/{job_id}/events"),
                    timeout=_event_read_timeout(deadline, self.timeout),
                    headers=headers,
                    stream=True,
                ) as response:
                    _validate_event_response(
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
                    for record in _iter_sse_job_events(
                        response.iter_lines(decode_unicode=True),
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
                            yield record
                        if terminal:
                            return
            except JobEventCursorGapError:
                raise
            except requests.RequestException:
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
            time.sleep(_event_retry_delay(deadline, attempts, jitter))

    def get_job_result(self, job_id: str) -> TerminalJobResult:
        try:
            response = requests.get(
                self._http_url(f"jobs/{job_id}/result"),
                timeout=self.timeout,
                headers=self._auth_headers,
            )
        except requests.RequestException as exc:
            err = f"Job result error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = (
                f"Failed to fetch job result. HTTP {response.status_code}: "
                f"{response.text}"
            )
            raise DownloadError(err)
        if "application/json" not in response.headers.get("content-type", ""):
            err = "Job result response was not JSON."
            raise DownloadError(err)
        return parse_job_result(response.json())

    def download_job_result_to_file(
        self,
        job_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        output_path = Path(path)
        try:
            with requests.get(
                self._http_url(f"jobs/{job_id}/result/download"),
                timeout=self.timeout,
                headers=self._auth_headers,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    err = (
                        "Failed to download job result. "
                        f"HTTP {response.status_code}: {response.text}"
                    )
                    raise DownloadError(err)

                if "application/json" in response.headers.get("content-type", ""):
                    result = parse_job_result(response.json())
                    err = (
                        f"Job {job_id} returned {result.status} JSON result, "
                        "not a file."
                    )
                    raise DownloadError(err)

                with output_path.open("wb") as file:
                    file.writelines(response.iter_content(chunk_size=65536))
        except requests.RequestException as exc:
            err = f"Job result download error: {exc}"
            raise DownloadError(err) from exc

    def get_result_descriptor(self, result_ref_or_job_id: str) -> ResultDescriptor:
        job_id = self._job_id_from_result_ref(result_ref_or_job_id)
        return self._request_model(
            "GET",
            f"jobs/{job_id}/result/descriptor",
            ResultDescriptor,
            error_context="fetch result descriptor",
        )

    def download_result(
        self,
        result_ref_or_job_id: str,
        path: str | os.PathLike[str],
        *,
        format: str = "jsonl",  # noqa: A002
    ) -> None:
        if format != "jsonl":
            err = "Only JSONL result downloads are supported. Use format='jsonl'."
            raise DownloadError(err)

        job_id = self._job_id_from_result_ref(result_ref_or_job_id)
        output_path = Path(path)
        try:
            with requests.get(
                self._http_url(f"jobs/{job_id}/result/table.jsonl"),
                timeout=self.timeout,
                headers=self._auth_headers,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    err = (
                        "Failed to download result. "
                        f"HTTP {response.status_code}: {response.text}"
                    )
                    raise DownloadError(err)

                with output_path.open("wb") as file:
                    file.writelines(response.iter_content(chunk_size=65536))
        except requests.RequestException as exc:
            err = f"Result download error: {exc}"
            raise DownloadError(err) from exc

    def result_dataframe(self, result_ref_or_job_id: str) -> pd.DataFrame:
        pandas = _load_pandas()
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        try:
            self.download_result(result_ref_or_job_id, temp_path, format="jsonl")
            return pandas.read_json(temp_path, lines=True)
        finally:
            temp_path.unlink(missing_ok=True)

    def get_data_types(self) -> DataTypesResponse:
        data_types_url = self._http_url("data-types")

        try:
            response = requests.get(
                data_types_url,
                timeout=self.timeout,
                headers=self.headers,
            )
        except requests.RequestException as exc:
            err = f"Data types request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = f"Failed to fetch data types. HTTP {response.status_code}"
            raise DownloadError(err)

        try:
            return DataTypesResponse.model_validate(response.json())
        except ValueError as exc:
            err = "Invalid data types response format"
            raise DownloadError(err) from exc

    def get_metrics(self) -> MetricCatalogResponse:
        try:
            response = requests.get(
                self._http_url("metrics"),
                timeout=self.timeout,
                headers=self.headers,
            )
        except requests.RequestException as exc:
            err = f"Metrics request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = f"Failed to fetch metrics. HTTP {response.status_code}"
            raise DownloadError(err)

        return MetricCatalogResponse.model_validate(response.json())

    def get_metric(self, metric_name: str) -> MetricInfoV4:
        try:
            response = requests.get(
                self._http_url(f"metrics/{metric_name}"),
                timeout=self.timeout,
                headers=self.headers,
            )
        except requests.RequestException as exc:
            err = f"Metric request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = f"Failed to fetch metric. HTTP {response.status_code}"
            raise DownloadError(err)

        return MetricInfoV4.model_validate(response.json())


class _SyncAdminTransport(_SyncTransport):
    """Synchronous transport configured exclusively with an administrator key."""


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

    def events(
        self,
        job_id: str,
        *,
        after_id: str | None = None,
        kinds: set[str] | None = None,
        timeout: float | None = None,
        max_reconnect_attempts: int = 5,
    ) -> Iterator[JobEventRecord]:
        return self._transport.iter_job_events(
            job_id,
            last_event_id=after_id,
            kinds=kinds,
            timeout=timeout,
            max_reconnect_attempts=max_reconnect_attempts,
        )


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
        format: str = "jsonl",  # noqa: A002
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
        return handle.wait(timeout=wait_seconds)

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
        if not isinstance(result, FileJobResult):
            err = f"Job {result.job_id} did not produce a file result."
            raise DownloadError(err)
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

    def create(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse:
        return self._transport.create_plugin_repo(
            source,
            repo_id=repo_id,
            enabled=enabled,
        )

    def update(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> UpdatePluginRepoResponse:
        return self._transport.update_plugin_repo(
            repo_id,
            source=source,
            enabled=enabled,
        )

    def delete(self, repo_id: str) -> DeletePluginRepoResponse:
        return self._transport.delete_plugin_repo(repo_id)

    def sync(self, repo_id: str) -> SyncPluginRepoResponse:
        return self._transport.sync_plugin_repo(repo_id)


class _AdminCatalogResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def summary(self) -> CatalogSummaryResponse:
        return self._transport.get_admin_catalog()

    def refresh(self) -> PluginCatalogRefreshResponse:
        return self._transport.refresh_plugin_catalog()


class _WorkerRestartOptions(TypedDict):
    timeout: NotRequired[float]


class _AdminWorkersResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> WorkersResponse:
        return self._transport.get_admin_workers()

    def get(self, name: str) -> WorkerDetail:
        return self._transport.get_admin_worker(name)

    def restart(
        self,
        **options: Unpack[_WorkerRestartOptions],
    ) -> WorkerRestartResponse:
        return self._transport.restart_workers(timeout=options.get("timeout", 30.0))


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

    def set(self, metric: str, queue: str) -> MetricQueueAssignmentResponse:
        return self._transport.set_plugin_routing(metric, queue)

    def delete(self, metric: str) -> DeleteMetricQueueResponse:
        return self._transport.delete_plugin_routing(metric)


class LyraClient:
    """Resource-oriented synchronous client for the Lyra HTTP API."""

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        agent_api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        transport = _SyncTransport(
            host,
            timeout,
            headers,
            api_key=agent_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        self.lookups = _LookupsResource(transport)
        self.catalog = _CatalogResource(transport)
        self.jobs = _JobsResource(transport)
        self.results = _ResultsResource(transport)
        self.raw = _RawMetricsResource(transport)


class LyraAdminClient:
    """Resource-oriented synchronous client for Lyra administration."""

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        admin_api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        transport = _SyncAdminTransport(
            host,
            timeout,
            headers,
            api_key=admin_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        self.jobs = _AdminJobsResource(transport)
        self.plugin_repos = _AdminPluginReposResource(transport)
        self.catalog = _AdminCatalogResource(transport)
        self.workers = _AdminWorkersResource(transport)
        self.queues = _AdminQueuesResource(transport)
        self.routing = _AdminRoutingResource(transport)

    def status(self) -> AdminStatusResponse:
        return self._transport.get_admin_status()

    def config_summary(self) -> ConfigSummaryResponse:
        return self._transport.get_admin_config_summary()
