"""Custom exceptions for Lyra API client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.sdk.models.job import CancelledJobResult, FailedJobResult


class LyraAPIError(Exception):
    """Base exception for all Lyra API errors."""


class DownloadError(LyraAPIError):
    """Transport, HTTP status, malformed JSON, or response validation failure.

    Messages identify the operation; transport and parsing causes are chained.
    Ordinary requests, including submissions, are not retried automatically.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        """Retain typed retry guidance without initiating retries."""
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class JobWaitTimeoutError(LyraAPIError):
    """The caller's wait deadline expired without cancelling the remote job."""

    def __init__(self, message: str, *, job_id: str) -> None:
        """Attach the job identity to a wait timeout."""
        super().__init__(message)
        self.job_id = job_id


class JobPollingError(LyraAPIError):
    """Consecutive transient observation retries were exhausted."""

    def __init__(self, message: str, *, job_id: str, attempts: int) -> None:
        """Attach job identity and retry count."""
        super().__init__(message)
        self.job_id = job_id
        self.attempts = attempts


class ServiceUnavailableError(LyraAPIError):
    """Structured unexpected HTTP 503 from an ordinary request or download.

    Retains the service code, retryable flag, and Retry-After guidance.
    It does not initiate retries. Readiness accepts HTTP 503 as a normal response;
    Job waits apply a bounded retry policy.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        retry_after_seconds: float | None,
    ) -> None:
        """Initialize an unavailable-service error with retry guidance."""
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class MetricRunError(LyraAPIError):
    """A submitted metric reached a failed or cancelled terminal state."""

    def __init__(self, result: FailedJobResult | CancelledJobResult) -> None:
        """Initialize the error from a failed or cancelled terminal result."""
        detail = result.error
        super().__init__(
            f"Metric job {result.job_id} finished with status {result.status}: {detail}"
        )
        self.job_id = result.job_id
        self.status = result.status
        self.error = detail
        self.result = result
