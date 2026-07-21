"""Custom exceptions for Lyra API client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.sdk.models import CancelledJobResult, FailedJobResult


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


class MetricRunError(LyraAPIError):
    """A submitted metric reached a failed or cancelled terminal state."""

    def __init__(self, result: FailedJobResult | CancelledJobResult) -> None:
        detail = result.error
        super().__init__(
            f"Metric job {result.job_id} finished with status {result.status}: {detail}"
        )
        self.job_id = result.job_id
        self.status = result.status
        self.error = detail
        self.result = result
