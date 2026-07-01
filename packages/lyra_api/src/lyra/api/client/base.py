import logging


class _BaseLyraAPIClient:
    """Base class for Lyra API clients, containing shared logic and configuration."""

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        admin_api_key: str | None = None,
        secure: bool = True,
        log_level: int = logging.INFO,
    ) -> None:
        """Initialize shared client configuration.

        Args:
            host: The API server hostname.
            timeout: Request timeout in seconds. Defaults to 30.0.
            headers: Default HTTP headers to include in HTTP requests. If None,
                defaults to an empty dict.
            admin_api_key: Admin bearer token to include in HTTP requests.
            secure: Whether to use HTTPS. Defaults to True.
            log_level: Logging level for status messages. Defaults to logging.INFO.
        """
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.headers = dict(headers or {})
        if admin_api_key is not None:
            self.headers["Authorization"] = f"Bearer {admin_api_key}"
        self.secure = secure
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._logger.setLevel(log_level)

    def _http_url(self, path: str) -> str:
        protocol = "https" if self.secure else "http"
        return f"{protocol}://{self.host}/{path.lstrip('/')}"
