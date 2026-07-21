from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, TypeVar, Unpack

import requests
from lyra.api.client.base import (
    _BaseLyraAPIClient,
    _load_pandas,
    service_unavailable_error,
)
from lyra.api.exceptions import DownloadError
from lyra.sdk.models import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    CreatePluginRepoRequest,
    CreatePluginRepoResponse,
    DataTypesResponse,
    DeleteMetricQueueResponse,
    DeletePluginRepoResponse,
    FileJobResult,
    JobCancelResponse,
    JobCreateResponse,
    JobEvent,
    JobLifecycleStatus,
    JobListResponse,
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
from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfoV3
from pydantic import BaseModel

if TYPE_CHECKING:
    import os
    from collections.abc import Iterable, Iterator

    import pandas as pd

TERMINAL_EVENTS = {"succeeded", "failed", "cancelled"}
_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _iter_sse_job_events(lines: Iterable[str | bytes]) -> Iterator[JobEvent]:
    data_lines: list[str] = []
    for line in lines:
        decoded_line = line.decode() if isinstance(line, bytes) else line
        if decoded_line == "":
            if data_lines:
                yield JobEvent.model_validate(json.loads("\n".join(data_lines)))
                data_lines = []
            continue
        if decoded_line.startswith(":"):
            continue

        field, separator, value = decoded_line.partition(":")
        if not separator:
            continue
        value = value.removeprefix(" ")
        if field == "data":
            data_lines.append(value)

    if data_lines:
        yield JobEvent.model_validate(json.loads("\n".join(data_lines)))


class _RequestModelOptions(TypedDict):
    error_context: str
    expected_status: NotRequired[int]
    params: NotRequired[dict[str, Any] | None]
    json_body: NotRequired[dict[str, Any] | None]


class LyraAPIClient(_BaseLyraAPIClient):
    """Synchronous client for the Lyra HTTP job API."""

    def _request_model(
        self,
        method: str,
        path: str,
        response_model: type[_ModelT],
        **options: Unpack[_RequestModelOptions],
    ) -> _ModelT:
        error_context = options["error_context"]
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
                headers=self._headers_for_path(path),
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
                headers=self._agent_headers,
            )
        except requests.RequestException as exc:
            err = f"Job creation error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 202:
            err = f"Failed to create job. HTTP {response.status_code}: {response.text}"
            raise DownloadError(err)
        return JobCreateResponse.model_validate(response.json())

    def get_job(self, job_id: str) -> JobStatusInfo:
        try:
            response = requests.get(
                self._http_url(f"jobs/{job_id}"),
                timeout=self.timeout,
                headers=self._agent_headers,
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
                headers=self._admin_headers,
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
                headers=self._admin_headers,
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
                headers=self._admin_headers,
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
                headers=self._admin_headers,
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
                headers=self._admin_headers,
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
                headers=self._admin_headers,
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
                headers=self._admin_headers,
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
                headers=self._admin_headers,
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
    ) -> Iterator[JobEvent]:
        headers = dict(self._agent_headers)
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id

        try:
            with requests.get(
                self._http_url(f"jobs/{job_id}/events"),
                timeout=self.timeout,
                headers=headers,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    err = (
                        "Failed to stream job events. "
                        f"HTTP {response.status_code}: {response.text}"
                    )
                    raise DownloadError(err)

                yield from _iter_sse_job_events(
                    response.iter_lines(decode_unicode=True)
                )
        except requests.RequestException as exc:
            err = f"Job event stream error: {exc}"
            raise DownloadError(err) from exc

    def get_job_result(self, job_id: str) -> TerminalJobResult:
        try:
            response = requests.get(
                self._http_url(f"jobs/{job_id}/result"),
                timeout=self.timeout,
                headers=self._agent_headers,
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
                headers=self._agent_headers,
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
                headers=self._agent_headers,
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

    def get_metric(self, metric_name: str) -> MetricInfoV3:
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

        return MetricInfoV3.model_validate(response.json())

    def _wait_for_terminal_event(self, job_id: str) -> JobEvent:
        for event in self.iter_job_events(job_id):
            if event.event in TERMINAL_EVENTS:
                return event
        err = f"Job {job_id} event stream ended before a terminal event."
        raise DownloadError(err)

    def process(
        self,
        metric: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> TableJobResult:
        job = self.create_job(metric, payload, idempotency_key=idempotency_key)
        self._wait_for_terminal_event(job.job_id)
        result = self.get_job_result(job.job_id)
        if result.status != "succeeded":
            err = (
                f"Job {job.job_id} finished with status {result.status}: {result.error}"
            )
            raise DownloadError(err)
        if not isinstance(result, TableJobResult):
            err = f"Job {job.job_id} produced a file result; use process_to_file()."
            raise DownloadError(err)
        return result

    def process_to_file(
        self,
        metric: str,
        payload: dict[str, Any],
        path: str | os.PathLike[str],
        *,
        idempotency_key: str | None = None,
    ) -> None:
        job = self.create_job(metric, payload, idempotency_key=idempotency_key)
        event = self._wait_for_terminal_event(job.job_id)
        result = parse_job_result(event.data)
        if result.status != "succeeded":
            err = (
                f"Job {job.job_id} finished with status {result.status}: {result.error}"
            )
            raise DownloadError(err)
        if not isinstance(result, FileJobResult):
            err = f"Job {job.job_id} did not produce a file result."
            raise DownloadError(err)
        self.download_job_result_to_file(job.job_id, path)
