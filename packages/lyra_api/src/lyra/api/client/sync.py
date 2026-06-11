import json
import os
from pathlib import Path
from typing import Any, overload

import requests
from lyra.api.client.base import _BaseLyraAPIClient
from lyra.api.exceptions import DownloadError, WebSocketError
from lyra.sdk.models.metric import MetricInfo
from websockets.sync.client import connect


class LyraAPIClient(_BaseLyraAPIClient):
    """Synchronous client for interacting with the Lyra API.

    This client handles two-step data processing:
    1. Submit a processing request via WebSocket and receive a download ID
    2. Download the processed data via HTTP GET using the download ID

    Attributes:
        host: The API server hostname.
        timeout: Request timeout in seconds.
        headers: Default HTTP headers to include in all requests.
        secure: Whether to use secure protocols (https/wss) or insecure (http/ws).
        log_level: Logging level for status messages. Defaults to logging.INFO.
    """

    def submit(self, metric: str, payload: dict) -> str:
        """Submit a processing request via WebSocket.

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
            with connect(
                ws_url,
                additional_headers=self.headers,
                **self.connect_kwargs,
            ) as websocket:
                websocket.send(json.dumps(payload))

                # Receive acknowledgment
                ack_str = websocket.recv()
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
                    notification_str = websocket.recv()
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

    def download(self, download_id: str) -> dict[str, Any]:
        """Download processed data via HTTP GET.

        Args:
            download_id: The download ID received from submit().

        Returns:
            The downloaded data as a dictionary.

        Raises:
            DownloadError: If the HTTP request fails.
        """
        download_url = self._http_url(f"download_result/{download_id}")

        try:
            response = requests.get(
                download_url,
                timeout=self.timeout,
                headers=self.headers,
            )
        except Exception as e:
            err = f"Download error: {e}"
            raise DownloadError(err) from e

        if response.status_code != 200:
            err = f"Failed to download data. HTTP {response.status_code}"
            raise DownloadError(err)

        return response.json()

    def download_to_file(self, download_id: str, path: str | os.PathLike[str]) -> None:
        """Download a result by ticket and write it to a file.

        Args:
            download_id: The download ID received from submit().
            path: The file path to write the result to.

        Raises:
            DownloadError: If the HTTP request fails.
        """
        path = Path(path)
        download_url = self._http_url(f"download_result/{download_id}")

        try:
            with requests.get(
                download_url,
                timeout=self.timeout,
                headers=self.headers,
                stream=True,
            ) as response:
                status_code = response.status_code
                if status_code == 200:
                    with path.open("wb") as f:
                        f.writelines(response.iter_content(chunk_size=65536))
                    return
        except Exception as e:
            err = f"Download error: {e}"
            raise DownloadError(err) from e

        err = f"Failed to download data. HTTP {status_code}"
        raise DownloadError(err)

    def get_data_types(self) -> list[dict[str, Any]]:
        """Fetch available data types from the API.

        Returns:
            A list of data type objects.

        Raises:
            DownloadError: If the HTTP request fails or returns an invalid payload.
        """
        data_types_url = self._http_url("data_types")

        try:
            response = requests.get(
                data_types_url,
                timeout=self.timeout,
                headers=self.headers,
            )
        except Exception as e:
            err = f"Data types request error: {e}"
            raise DownloadError(err) from e

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
    def get_metrics(
        self, metric_name: None = None, *, prettify_types: bool = True
    ) -> list[MetricInfo]: ...

    @overload
    def get_metrics(
        self, metric_name: str, *, prettify_types: bool = True
    ) -> MetricInfo: ...

    def get_metrics(
        self,
        metric_name: str | None = None,
        *,
        prettify_types: bool = True,
    ) -> list[MetricInfo] | MetricInfo:
        """Fetch available metrics from the API.

        Returns:
            A list of MetricInfo objects if no specific metric_name is provided,
            otherwise a single MetricInfo object.

        Raises:
            DownloadError: If the HTTP request fails or returns an invalid payload.
        """
        metric_str = "" if metric_name is None else metric_name
        metrics_url = self._http_url(f"metrics/{metric_str}")

        try:
            response = requests.get(
                metrics_url,
                timeout=self.timeout,
                headers=self.headers,
                params={"prettify_types": prettify_types},
            )
        except Exception as e:
            err = f"Metrics request error: {e}"
            raise DownloadError(err) from e

        if response.status_code != 200:
            err = f"Failed to fetch metrics. HTTP {response.status_code}"
            raise DownloadError(err)

        metrics = response.json()

        return (
            [MetricInfo.model_validate(item) for item in metrics]
            if metric_name is None
            else MetricInfo.model_validate(metrics)
        )

    def process(self, metric: str, payload: dict) -> dict[str, Any]:
        """Submit a request and download the result in one call.

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
        download_id = self.submit(metric, payload)

        self._logger.info("Downloading data via HTTP...")
        return self.download(download_id)

    def process_to_file(
        self,
        metric: str,
        payload: dict,
        path: str | os.PathLike[str],
    ) -> None:
        """Submit a request and write the result to a file.

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
        download_id = self.submit(metric, payload)

        self._logger.info("Downloading data to file...")
        self.download_to_file(download_id, path)
