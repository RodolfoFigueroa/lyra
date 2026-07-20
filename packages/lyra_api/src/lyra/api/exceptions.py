"""Custom exceptions for Lyra API client."""


class LyraAPIError(Exception):
    """Base exception for all Lyra API errors."""


class DownloadError(LyraAPIError):
    """Exception raised for download/HTTP-related errors."""


class ServiceUnavailableError(LyraAPIError):
    """Structured retryable service-unavailable response from Lyra."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        retry_after_seconds: int | None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
