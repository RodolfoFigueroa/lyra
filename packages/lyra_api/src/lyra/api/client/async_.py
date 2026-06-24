import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, overload

import aiofiles
import aiohttp
from lyra.api.client.base import _BaseLyraAPIClient
from lyra.api.exceptions import DownloadError
from lyra.sdk.models import JobCreateResponse, JobEvent, JobResult, JobStatusInfo
from lyra.sdk.models.metric import MetricInfoV2

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

    async def get_job_result(self, job_id: str) -> JobResult:
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
                    err = "Job result is a file; use download_job_result_to_file()."
                    raise DownloadError(err)
                return JobResult.model_validate(await response.json())
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
                    self._http_url(f"jobs/{job_id}/result"),
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
                    result = JobResult.model_validate(await response.json())
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

    async def get_data_types(self) -> list[dict[str, Any]]:
        data_types_url = self._http_url("data_types")
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

        if not isinstance(data_types, list) or not all(
            isinstance(item, dict) for item in data_types
        ):
            err = "Invalid data types response format"
            raise DownloadError(err)

        return data_types

    @overload
    async def get_metrics(self, metric_name: None = None) -> list[MetricInfoV2]: ...

    @overload
    async def get_metrics(self, metric_name: str) -> MetricInfoV2: ...

    async def get_metrics(
        self,
        metric_name: str | None = None,
    ) -> list[MetricInfoV2] | MetricInfoV2:
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
            [MetricInfoV2.model_validate(item) for item in metrics]
            if metric_name is None
            else MetricInfoV2.model_validate(metrics)
        )

    async def _wait_for_terminal_event(self, job_id: str) -> JobEvent:
        async for event in self.iter_job_events(job_id):
            if event.event in TERMINAL_EVENTS:
                return event
        err = f"Job {job_id} event stream ended before a terminal event."
        raise DownloadError(err)

    async def process(self, metric: str, payload: dict[str, Any]) -> Any:
        job = await self.create_job(metric, payload)
        await self._wait_for_terminal_event(job.job_id)
        result = await self.get_job_result(job.job_id)
        if result.status != "succeeded":
            err = (
                f"Job {job.job_id} finished with status {result.status}: {result.error}"
            )
            raise DownloadError(err)
        return result.result

    async def process_to_file(
        self,
        metric: str,
        payload: dict[str, Any],
        path: str | os.PathLike[str],
    ) -> None:
        job = await self.create_job(metric, payload)
        event = await self._wait_for_terminal_event(job.job_id)
        result = JobResult.model_validate(event.data)
        if result.status != "succeeded":
            err = (
                f"Job {job.job_id} finished with status {result.status}: {result.error}"
            )
            raise DownloadError(err)
        if result.result_type != "file":
            err = f"Job {job.job_id} did not produce a file result."
            raise DownloadError(err)
        await self.download_job_result_to_file(job.job_id, path)
