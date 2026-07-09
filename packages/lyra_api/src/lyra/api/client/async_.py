import json
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any, TypeVar

import aiofiles
import aiofiles.os
import aiohttp
from lyra.api.client.base import _BaseLyraAPIClient, _load_pandas
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
    HealthResponse,
    JobCancelResponse,
    JobCreateResponse,
    JobEvent,
    JobLifecycleStatus,
    JobListResponse,
    JobStatusInfo,
    MetricQueueAssignmentResponse,
    MetZoneCodeResponse,
    PluginCatalogRefreshResponse,
    PluginRepoListResponse,
    PluginRoutingResponse,
    QueuesResponse,
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

TERMINAL_EVENTS = {"succeeded", "failed", "cancelled"}
_ModelT = TypeVar("_ModelT", bound=BaseModel)


async def _aiter_sse_job_events(lines: AsyncIterator[str]) -> AsyncIterator[JobEvent]:
    data_lines: list[str] = []
    async for line in lines:
        if line == "":
            if data_lines:
                yield JobEvent.model_validate(json.loads("\n".join(data_lines)))
                data_lines = []
            continue
        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.removeprefix(" ")
        if field == "data":
            data_lines.append(value)

    if data_lines:
        yield JobEvent.model_validate(json.loads("\n".join(data_lines)))


async def _response_lines(response: aiohttp.ClientResponse) -> AsyncIterator[str]:
    async for raw_line in response.content:
        yield raw_line.decode().rstrip("\r\n")


class AsyncLyraAPIClient(_BaseLyraAPIClient):
    """Asynchronous client for the Lyra HTTP job API."""

    async def _request_model(
        self,
        method: str,
        path: str,
        response_model: type[_ModelT],
        *,
        error_context: str,
        expected_status: int = 200,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> _ModelT:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.request(
                    method,
                    self._http_url(path),
                    params=params,
                    json=json_body,
                    headers=self.headers,
                ) as response,
            ):
                if response.status != expected_status:
                    text = await response.text()
                    err = f"Failed to {error_context}. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return response_model.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"{error_context} request error: {exc}"
            raise DownloadError(err) from exc

    async def get_health(self) -> HealthResponse:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("health"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to fetch health. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return HealthResponse.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Health request error: {exc}"
            raise DownloadError(err) from exc

    async def get_met_zone_code(self, name: str) -> MetZoneCodeResponse:
        return await self._request_model(
            "GET",
            "lookups/met-zones",
            MetZoneCodeResponse,
            error_context="fetch met-zone lookup",
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

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._http_url("jobs"),
                    json=body,
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 202:
                    text = await response.text()
                    err = f"Failed to create job. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return JobCreateResponse.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Job creation error: {exc}"
            raise DownloadError(err) from exc

    async def get_job(self, job_id: str) -> JobStatusInfo:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to fetch job. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return JobStatusInfo.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Job status error: {exc}"
            raise DownloadError(err) from exc

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
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/jobs"),
                    params=params,
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to list admin jobs. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return JobListResponse.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Admin job list error: {exc}"
            raise DownloadError(err) from exc

    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._http_url(f"admin/jobs/{job_id}/cancel"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to cancel admin job. HTTP {response.status}: {text}"
                    raise DownloadError(err)
                return JobCancelResponse.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Admin job cancellation error: {exc}"
            raise DownloadError(err) from exc

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
        timeout: float = 30.0,  # noqa: ASYNC109
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
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/status"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin status. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return AdminStatusResponse.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Admin status request error: {exc}"
            raise DownloadError(err) from exc

    async def get_admin_config_summary(self) -> ConfigSummaryResponse:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/config-summary"),
                    headers=self.headers,
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
        except aiohttp.ClientError as exc:
            err = f"Admin config summary request error: {exc}"
            raise DownloadError(err) from exc

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/catalog"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin catalog. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return CatalogSummaryResponse.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Admin catalog request error: {exc}"
            raise DownloadError(err) from exc

    async def get_admin_workers(self) -> WorkersResponse:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/workers"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin workers. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return WorkersResponse.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Admin workers request error: {exc}"
            raise DownloadError(err) from exc

    async def get_admin_worker(self, worker_name: str) -> WorkerDetail:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"admin/workers/{worker_name}"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin worker. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return WorkerDetail.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Admin worker request error: {exc}"
            raise DownloadError(err) from exc

    async def get_admin_queues(self) -> QueuesResponse:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url("admin/queues"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = (
                        f"Failed to fetch admin queues. HTTP {response.status}: {text}"
                    )
                    raise DownloadError(err)
                return QueuesResponse.model_validate(await response.json())
        except aiohttp.ClientError as exc:
            err = f"Admin queues request error: {exc}"
            raise DownloadError(err) from exc

    async def iter_job_events(
        self,
        job_id: str,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[JobEvent]:
        headers = dict(self.headers)
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}/events"),
                    headers=headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to stream job events. HTTP {response.status}: {text}"
                    raise DownloadError(err)

                async for event in _aiter_sse_job_events(_response_lines(response)):
                    yield event
        except aiohttp.ClientError as exc:
            err = f"Job event stream error: {exc}"
            raise DownloadError(err) from exc

    async def get_job_result(self, job_id: str) -> TerminalJobResult:
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}/result"),
                    headers=self.headers,
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
        except aiohttp.ClientError as exc:
            err = f"Job result error: {exc}"
            raise DownloadError(err) from exc

    async def download_job_result_to_file(
        self,
        job_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        output_path = Path(path)
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}/result/download"),
                    headers=self.headers,
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
        except aiohttp.ClientError as exc:
            err = f"Job result download error: {exc}"
            raise DownloadError(err) from exc

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
        format: str = "jsonl",  # noqa: A002
    ) -> None:
        if format != "jsonl":
            err = "Only JSONL result downloads are supported. Use format='jsonl'."
            raise DownloadError(err)

        job_id = self._job_id_from_result_ref(result_ref_or_job_id)
        output_path = Path(path)
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(f"jobs/{job_id}/result/table.jsonl"),
                    headers=self.headers,
                ) as response,
            ):
                if response.status != 200:
                    text = await response.text()
                    err = f"Failed to download result. HTTP {response.status}: {text}"
                    raise DownloadError(err)

                async with aiofiles.open(output_path, "wb") as file:
                    async for chunk in response.content.iter_chunked(65536):
                        await file.write(chunk)
        except aiohttp.ClientError as exc:
            err = f"Result download error: {exc}"
            raise DownloadError(err) from exc

    async def result_dataframe(self, result_ref_or_job_id: str) -> Any:
        pandas = _load_pandas()
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

    async def get_metric(self, metric_name: str) -> MetricInfoV3:
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

        return MetricInfoV3.model_validate(metric)

    async def _wait_for_terminal_event(self, job_id: str) -> JobEvent:
        async for event in self.iter_job_events(job_id):
            if event.event in TERMINAL_EVENTS:
                return event
        err = f"Job {job_id} event stream ended before a terminal event."
        raise DownloadError(err)

    async def process(self, metric: str, payload: dict[str, Any]) -> TableJobResult:
        job = await self.create_job(metric, payload)
        await self._wait_for_terminal_event(job.job_id)
        result = await self.get_job_result(job.job_id)
        if result.status != "succeeded":
            err = (
                f"Job {job.job_id} finished with status {result.status}: {result.error}"
            )
            raise DownloadError(err)
        if not isinstance(result, TableJobResult):
            err = f"Job {job.job_id} produced a file result; use process_to_file()."
            raise DownloadError(err)
        return result

    async def process_to_file(
        self,
        metric: str,
        payload: dict[str, Any],
        path: str | os.PathLike[str],
    ) -> None:
        job = await self.create_job(metric, payload)
        event = await self._wait_for_terminal_event(job.job_id)
        result = parse_job_result(event.data)
        if result.status != "succeeded":
            err = (
                f"Job {job.job_id} finished with status {result.status}: {result.error}"
            )
            raise DownloadError(err)
        if not isinstance(result, FileJobResult):
            err = f"Job {job.job_id} did not produce a file result."
            raise DownloadError(err)
        await self.download_job_result_to_file(job.job_id, path)
