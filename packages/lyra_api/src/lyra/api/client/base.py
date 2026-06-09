import logging
from typing import Any

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
        connect_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize shared client configuration.

        Args:
            host: The API server hostname.
            timeout: Request timeout in seconds. Defaults to 100.0.
            headers: Default HTTP headers to include in WebSocket and HTTP requests.
                If None, defaults to an empty dict.
            secure: Whether to use secure protocols (https/wss). Defaults to True.
            log_level: Logging level for status messages. Defaults to logging.INFO.
            connect_kwargs: Additional keyword arguments passed to the WebSocket
                connect call. If omitted, the websocket handshake timeout
                defaults to ``timeout``.
        """
        self.host = host
        self.timeout = timeout
        self.headers = headers or {}
        self.secure = secure
        self.connect_kwargs = self._build_connect_kwargs(connect_kwargs)
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._logger.setLevel(log_level)

    def _http_url(self, path: str) -> str:
        protocol = "https" if self.secure else "http"
        return f"{protocol}://{self.host}/{path}"

    def _ws_url(self, path: str) -> str:
        protocol = "wss" if self.secure else "ws"
        return f"{protocol}://{self.host}/{path}"

    def _build_connect_kwargs(
        self,
        connect_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build websocket connect kwargs with client timeout defaults.

        Args:
            connect_kwargs: Optional websocket keyword arguments provided by the
                caller.

        Returns:
            A copy of the connect kwargs with a default handshake timeout.
        """
        resolved_kwargs = dict(connect_kwargs or {})
        resolved_kwargs.setdefault("open_timeout", self.timeout)
        return resolved_kwargs

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
