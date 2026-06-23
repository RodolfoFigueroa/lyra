import json
import os
from pathlib import Path
from typing import Any, overload

import aiofiles
import aiohttp
from lyra.api.client.base import _BaseLyraAPIClient
from lyra.api.exceptions import DownloadError, WebSocketError
from lyra.sdk.models.metric import MetricInfoV2
from websockets.asyncio.client import connect as async_connect


class AsyncLyraAPIClient(_BaseLyraAPIClient):
    """Asynchronous client for interacting with the Lyra API.

    This client handles two-step data processing with async/await support:
    1. Submit a processing request via WebSocket and receive a download ID
    2. Download the processed data via HTTP GET using the download ID

    Attributes:
        host: The API server hostname.
        timeout: Request timeout in seconds.
        headers: Default HTTP headers to include in all requests.
        secure: Whether to use secure protocols (https/wss) or insecure (http/ws).
        log_level: Logging level for status messages. Defaults to logging.INFO.
    """

    async def submit(self, metric: str, payload: dict) -> str:
        """Submit a processing request via WebSocket (async).

        Args:
            metric: The metric identifier for the processing task.
            payload: The data payload to process.

        Returns:
            The download ID for retrieving the processed result.

        Raises:
            WebSocketError: If the WebSocket connection fails or the server
                returns an error status.
        """
        ws_url = self._ws_url(f"ws/{metric}")

        error: str | None = None
        download_id: str | None = None

        try:
            async with async_connect(
                ws_url,
                additional_headers=self.headers,
                **self.connect_kwargs,
            ) as websocket:
                await websocket.send(json.dumps(payload))

                # Receive acknowledgment
                ack_str = await websocket.recv()
                ack = json.loads(ack_str)

                if ack["status"] == "error":
                    message = ack.get("message", "Unknown error")
                    error = f"Server error: {message}"
                else:
                    self._logger.info(
                        "Server acknowledged. Task ID: %s",
                        ack.get("task_id"),
                    )

                    # Receive processing result
                    notification_str = await websocket.recv()
                    notification = json.loads(notification_str)
                    status = notification["status"]

                    if status == "error":
                        message = notification.get("message", "Unknown error")
                        error = f"Worker failed: {message}"
                    elif status == "success":
                        download_id = notification.get("download_id")
                        self._logger.info(
                            "Worker finished. Received download ticket: %s",
                            download_id,
                        )
                    else:
                        error = f"Unexpected status: {status}"

        except Exception as e:
            err = f"WebSocket error: {e}"
            raise WebSocketError(err) from e

        if error:
            raise WebSocketError(error)

        if download_id is None:
            err = "Server did not return a download ID"
            raise WebSocketError(err)

        return download_id

    async def download(self, download_id: str) -> dict[str, Any]:
        """Download processed data via HTTP GET (async).

        Args:
            download_id: The download ID received from submit().

        Returns:
            The downloaded data as a dictionary.

        Raises:
            DownloadError: If the HTTP request fails.
        """
        download_url = self._http_url(f"download_result/{download_id}")
        status: int = 0

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    download_url,
                    headers=self.headers,
                ) as response,
            ):
                status = response.status
                if status == 200:
                    return await response.json()
        except Exception as e:
            err = f"Download error: {e}"
            raise DownloadError(err) from e

        err = f"Failed to download data. HTTP {status}"
        raise DownloadError(err)

    async def download_to_file(
        self,
        download_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        """Download a result by ticket and write it to a file (async).

        Args:
            download_id: The download ID received from submit().
            path: The file path to write the result to.

        Raises:
            DownloadError: If the HTTP request fails.
        """
        path = Path(path)
        download_url = self._http_url(f"download_result/{download_id}")
        status: int = 0

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    download_url,
                    headers=self.headers,
                ) as response,
            ):
                status = response.status
                if status == 200:
                    async with aiofiles.open(path, "wb") as f:
                        async for chunk in response.content.iter_chunked(65536):
                            await f.write(chunk)
                    return
        except Exception as e:
            err = f"Download error: {e}"
            raise DownloadError(err) from e

        err = f"Failed to download data. HTTP {status}"
        raise DownloadError(err)

    async def get_data_types(self) -> list[dict[str, Any]]:
        """Fetch available data types from the API (async).

        Returns:
            A list of data type objects.

        Raises:
            DownloadError: If the HTTP request fails or returns an invalid payload.
        """
        data_types_url = self._http_url("data_types")
        status: int = 0
        data_types: list | None = None

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
        except Exception as e:
            err = f"Data types request error: {e}"
            raise DownloadError(err) from e

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
        """Fetch available metrics from the API (async).

        Args:
            metric_name: Optional name of a specific metric to fetch. If None,
                returns a list of all metrics. If provided, returns the metric
                with the matching name.

        Returns:
            A list of metric objects.

        Raises:
            DownloadError: If the HTTP request fails or returns an invalid payload.
        """
        metric_str = "" if metric_name is None else metric_name
        metrics_url = self._http_url(f"metrics/{metric_str}")
        metrics: Any = None

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
        except Exception as e:
            err = f"Metrics request error: {e}"
            raise DownloadError(err) from e

        if status != 200:
            err = f"Failed to fetch metrics. HTTP {status}"
            raise DownloadError(err)

        return (
            [MetricInfoV2.model_validate(item) for item in metrics]
            if metric_name is None
            else MetricInfoV2.model_validate(metrics)
        )

    async def process(self, metric: str, payload: dict) -> dict[str, Any]:
        """Submit a request and download the result in one call (async).

        This is a convenience method combining submit() and download().

        Args:
            metric: The metric identifier for the processing task.
            payload: The data payload to process.

        Returns:
            The processed data as a dictionary.

        Raises:
            WebSocketError: If the submission step fails.
            DownloadError: If the download step fails.
        """
        self._logger.info("Submitting processing request...")
        download_id = await self.submit(metric, payload)

        self._logger.info("Downloading data via HTTP...")
        return await self.download(download_id)

    async def process_to_file(
        self,
        metric: str,
        payload: dict,
        path: str | os.PathLike[str],
    ) -> None:
        """Submit a request and write the result to a file (async).

        This is a convenience method combining submit() and a file download.
        Unlike process(), the result is written directly to disk rather than
        loaded into memory.

        Args:
            metric: The metric identifier for the processing task.
            payload: The data payload to process.
            path: The file path to write the result to.

        Raises:
            WebSocketError: If the submission step fails.
            DownloadError: If the download step fails.
        """
        self._logger.info("Submitting processing request...")
        download_id = await self.submit(metric, payload)

        self._logger.info("Downloading data to file...")
        await self.download_to_file(download_id, path)
