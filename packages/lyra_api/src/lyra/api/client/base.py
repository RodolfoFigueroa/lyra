import importlib
import logging
from types import ModuleType
from urllib.parse import urlparse

from lyra.api.exceptions import DownloadError


def parse_result_ref(result_ref_or_job_id: str) -> str:
    """Return the job id from a Lyra result reference or raw job id."""

    value = result_ref_or_job_id.strip()
    if not value:
        err = "Result reference or job id must be a non-empty string."
        raise DownloadError(err)

    if value.startswith("lyra://"):
        parsed = urlparse(value)
        if (
            parsed.scheme != "lyra"
            or parsed.netloc != "results"
            or not parsed.path.startswith("/")
            or parsed.path == "/"
            or "/" in parsed.path.removeprefix("/")
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            err = "Invalid Lyra result reference. Expected 'lyra://results/{job_id}'."
            raise DownloadError(err)
        return parsed.path.removeprefix("/")

    if "://" in value:
        err = "Unsupported result reference. Expected 'lyra://results/{job_id}'."
        raise DownloadError(err)

    return value


def _load_pandas() -> ModuleType:
    try:
        return importlib.import_module("pandas")
    except ImportError as exc:
        err = (
            "pandas is required for result_dataframe(); install pandas or use "
            "download_result(..., format='jsonl') instead."
        )
        raise DownloadError(err) from exc


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

    def _job_id_from_result_ref(self, result_ref_or_job_id: str) -> str:
        return parse_result_ref(result_ref_or_job_id)
