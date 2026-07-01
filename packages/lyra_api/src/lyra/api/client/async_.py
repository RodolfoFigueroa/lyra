import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, overload

import aiofiles
import aiohttp
from lyra.api.client.base import _BaseLyraAPIClient
from lyra.api.exceptions import DownloadError
from lyra.sdk.models import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    DataTypesResponse,
    FileJobResult,
    HealthResponse,
    JobCancelResponse,
    JobCreateResponse,
    JobEvent,
    JobLifecycleStatus,
    JobListResponse,
    JobStatusInfo,
    QueuesResponse,
    TableJobResult,
    TerminalJobResult,
    WorkerDetail,
    WorkersResponse,
    parse_job_result,
)
from lyra.sdk.models.metric import MetricInfoV3

TERMINAL_EVENTS = {"succeeded", "failed", "cancelled"}


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

    @overload
    async def get_metrics(self, metric_name: None = None) -> list[MetricInfoV3]: ...

    @overload
    async def get_metrics(self, metric_name: str) -> MetricInfoV3: ...

    async def get_metrics(
        self,
        metric_name: str | None = None,
    ) -> list[MetricInfoV3] | MetricInfoV3:
        metric_str = "" if metric_name is None else metric_name
        metrics_url = self._http_url(f"metrics/{metric_str}")
        metrics: Any = None
        status: int = 0

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    metrics_url,
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

        return (
            [MetricInfoV3.model_validate(item) for item in metrics]
            if metric_name is None
            else MetricInfoV3.model_validate(metrics)
        )

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
