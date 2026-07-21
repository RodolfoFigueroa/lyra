"""Shared request construction and response handling for API clients."""

import importlib
import logging
from types import ModuleType
from urllib.parse import urlparse

from lyra.api.exceptions import DownloadError, ServiceUnavailableError


def service_unavailable_error(
    payload: object,
    retry_after: str | None,
) -> ServiceUnavailableError | None:
    """Decode a structured service-unavailable response when present.

    Returns:
        A typed service error, or ``None`` for an unrecognized payload.
    """
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
    """Return the job id from a Lyra result reference or raw job id.

    Raises:
        DownloadError: If the value is empty or uses an invalid reference URI.
    """
    value = result_ref_or_job_id.strip()
    if not value:
        err = "Result reference or job id must be a non-empty string."
        raise DownloadError(err)

    if value.startswith("lyra://"):
        parsed = urlparse(value)
        valid_path = (
            parsed.path.startswith("/")
            and parsed.path != "/"
            and "/" not in parsed.path.removeprefix("/")
        )
        has_suffix = any((parsed.params, parsed.query, parsed.fragment))
        if (
            parsed.scheme != "lyra"
            or parsed.netloc != "results"
            or not valid_path
            or has_suffix
        ):
            err = "Invalid Lyra result reference. Expected 'lyra://results/{job_id}'."
            raise DownloadError(err)
        return parsed.path.removeprefix("/")

    if "://" in value:
        err = "Unsupported result reference. Expected 'lyra://results/{job_id}'."
        raise DownloadError(err)

    return value


def load_pandas() -> ModuleType:
    """Load pandas for optional DataFrame result hydration.

    Returns:
        The imported pandas module.

    Raises:
        DownloadError: If pandas is not installed.
    """
    try:
        return importlib.import_module("pandas")
    except ImportError as exc:
        err = (
            "pandas is required for result_dataframe(); install pandas or use "
            "download_result(..., format='jsonl') instead."
        )
        raise DownloadError(err) from exc


class BaseTransport:
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

    @staticmethod
    def _job_id_from_result_ref(result_ref_or_job_id: str) -> str:
        return parse_result_ref(result_ref_or_job_id)
