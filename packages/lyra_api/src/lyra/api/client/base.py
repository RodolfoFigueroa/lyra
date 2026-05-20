import logging

from lyra.api.exceptions import DownloadError


class _BaseLyraAPIClient:
    """Base class for Lyra API clients, containing shared logic and configuration."""

    def __init__(
        self,
        host: str,
        timeout: float = 100.0,
        headers: dict[str, str] | None = None,
        *,
        secure: bool = True,
        log_level: int = logging.INFO,
    ) -> None:
        self.host = host
        self.timeout = timeout
        self.headers = headers or {}
        self.secure = secure
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._logger.setLevel(log_level)

    def _http_url(self, path: str) -> str:
        protocol = "https" if self.secure else "http"
        return f"{protocol}://{self.host}/{path}"

    def _ws_url(self, path: str) -> str:
        protocol = "wss" if self.secure else "ws"
        return f"{protocol}://{self.host}/{path}"

    def _validate_metric_response(
        self,
        response: list | dict,
        metric_name: str | None,
    ) -> None:
        """Validate the format of the metrics response.

        Args:
            response: The raw response data to validate.
            metric_name: The metric name used in the request, or None for all metrics.

        Raises:
            DownloadError: If the response format is invalid.
        """
        if metric_name is None:
            if not isinstance(response, list) or not all(
                isinstance(item, dict) for item in response
            ):
                err = "Invalid metrics response format"
                raise DownloadError(err)
        elif not isinstance(response, dict):
            err = "Invalid metric response format"
            raise DownloadError(err)
