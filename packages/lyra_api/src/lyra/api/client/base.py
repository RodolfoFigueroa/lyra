import importlib
import logging
from types import ModuleType
from urllib.parse import urlparse

from lyra.api.exceptions import DownloadError, ServiceUnavailableError


def service_unavailable_error(
    payload: object,
    retry_after: str | None,
) -> ServiceUnavailableError | None:
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    message = detail.get("message")
    retryable = detail.get("retryable")
    if not isinstance(code, str) or not isinstance(message, str):
        return None
    if not isinstance(retryable, bool):
        return None
    try:
        retry_after_seconds = int(retry_after) if retry_after is not None else None
    except ValueError:
        retry_after_seconds = None
    return ServiceUnavailableError(
        message,
        code=code,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
    )


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


class _BaseTransport:
    """Base class for Lyra API clients, containing shared logic and configuration."""

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Initialize shared client configuration.

        Args:
            host: The API server hostname.
            timeout: Request timeout in seconds. Defaults to 30.0.
            headers: Default HTTP headers to include in HTTP requests. If None,
                defaults to an empty dict.
            api_key: Bearer token used by this transport's protected routes.
            secure: Whether to use HTTPS. Defaults to True.

        """
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.headers = dict(headers or {})
        self._auth_headers = dict(self.headers)
        if api_key is not None:
            self._auth_headers["Authorization"] = f"Bearer {api_key}"
        self.secure = secure
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._logger.setLevel(logging.INFO)

    def _http_url(self, path: str) -> str:
        protocol = "https" if self.secure else "http"
        return f"{protocol}://{self.host}/{path.lstrip('/')}"

    def _job_id_from_result_ref(self, result_ref_or_job_id: str) -> str:
        return parse_result_ref(result_ref_or_job_id)
