import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, overload

import requests
from lyra.api.client.base import _BaseLyraAPIClient
from lyra.api.exceptions import DownloadError
from lyra.sdk.models import JobCreateResponse, JobEvent, JobResult, JobStatusInfo
from lyra.sdk.models.metric import MetricInfoV2

TERMINAL_EVENTS = {"succeeded", "failed", "cancelled"}


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


class LyraAPIClient(_BaseLyraAPIClient):
    """Synchronous client for the Lyra HTTP job API."""

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
                headers=self.headers,
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
                headers=self.headers,
            )
        except requests.RequestException as exc:
            err = f"Job status error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = f"Failed to fetch job. HTTP {response.status_code}: {response.text}"
            raise DownloadError(err)
        return JobStatusInfo.model_validate(response.json())

    def iter_job_events(
        self,
        job_id: str,
        *,
        last_event_id: str | None = None,
    ) -> Iterator[JobEvent]:
        headers = dict(self.headers)
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

    def get_job_result(self, job_id: str) -> JobResult:
        try:
            response = requests.get(
                self._http_url(f"jobs/{job_id}/result"),
                timeout=self.timeout,
                headers=self.headers,
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
            err = "Job result is a file; use download_job_result_to_file()."
            raise DownloadError(err)
        return JobResult.model_validate(response.json())

    def download_job_result_to_file(
        self,
        job_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        output_path = Path(path)
        try:
            with requests.get(
                self._http_url(f"jobs/{job_id}/result"),
                timeout=self.timeout,
                headers=self.headers,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    err = (
                        "Failed to download job result. "
                        f"HTTP {response.status_code}: {response.text}"
                    )
                    raise DownloadError(err)

                if "application/json" in response.headers.get("content-type", ""):
                    result = JobResult.model_validate(response.json())
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

    def get_data_types(self) -> list[dict[str, Any]]:
        data_types_url = self._http_url("data_types")

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

        data_types = response.json()
        if not isinstance(data_types, list) or not all(
            isinstance(item, dict) for item in data_types
        ):
            err = "Invalid data types response format"
            raise DownloadError(err)

        return data_types

    @overload
    def get_metrics(self, metric_name: None = None) -> list[MetricInfoV2]: ...

    @overload
    def get_metrics(self, metric_name: str) -> MetricInfoV2: ...

    def get_metrics(
        self,
        metric_name: str | None = None,
    ) -> list[MetricInfoV2] | MetricInfoV2:
        metric_str = "" if metric_name is None else metric_name
        metrics_url = self._http_url(f"metrics/{metric_str}")

        try:
            response = requests.get(
                metrics_url,
                timeout=self.timeout,
                headers=self.headers,
            )
        except requests.RequestException as exc:
            err = f"Metrics request error: {exc}"
            raise DownloadError(err) from exc

        if response.status_code != 200:
            err = f"Failed to fetch metrics. HTTP {response.status_code}"
            raise DownloadError(err)

        metrics = response.json()
        return (
            [MetricInfoV2.model_validate(item) for item in metrics]
            if metric_name is None
            else MetricInfoV2.model_validate(metrics)
        )

    def _wait_for_terminal_event(self, job_id: str) -> JobEvent:
        for event in self.iter_job_events(job_id):
            if event.event in TERMINAL_EVENTS:
                return event
        err = f"Job {job_id} event stream ended before a terminal event."
        raise DownloadError(err)

    def process(self, metric: str, payload: dict[str, Any]) -> Any:
        job = self.create_job(metric, payload)
        self._wait_for_terminal_event(job.job_id)
        result = self.get_job_result(job.job_id)
        if result.status != "succeeded":
            err = (
                f"Job {job.job_id} finished with status {result.status}: {result.error}"
            )
            raise DownloadError(err)
        return result.result

    def process_to_file(
        self,
        metric: str,
        payload: dict[str, Any],
        path: str | os.PathLike[str],
    ) -> None:
        job = self.create_job(metric, payload)
        event = self._wait_for_terminal_event(job.job_id)
        result = JobResult.model_validate(event.data)
        if result.status != "succeeded":
            err = (
                f"Job {job.job_id} finished with status {result.status}: {result.error}"
            )
            raise DownloadError(err)
        if result.result_type != "file":
            err = f"Job {job.job_id} did not produce a file result."
            raise DownloadError(err)
        self.download_job_result_to_file(job.job_id, path)
