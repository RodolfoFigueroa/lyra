"""Custom exceptions for Lyra API client."""


class LyraAPIError(Exception):
    """Base exception for all Lyra API errors."""


class DownloadError(LyraAPIError):
    """Exception raised for download/HTTP-related errors."""


class JobEventStreamError(DownloadError):
    """Raised when a job event stream cannot be resumed."""

    def __init__(
        self,
        message: str,
        *,
        job_id: str,
        last_event_id: str | None,
        attempts: int,
    ) -> None:
        super().__init__(message)
        self.job_id = job_id
        self.last_event_id = last_event_id
        self.attempts = attempts


class JobEventCursorGapError(JobEventStreamError):
    """Raised when retained events no longer include a requested cursor."""


class JobWaitTimeoutError(JobEventStreamError):
    """Raised when waiting for a job exceeds its caller-provided deadline."""


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
