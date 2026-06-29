from datetime import datetime
from typing import Any, Literal

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field

JobLifecycleStatus = Literal[
    "queued",
    "started",
    "progress",
    "succeeded",
    "failed",
    "cancelled",
]
TerminalJobStatus = Literal["succeeded", "failed", "cancelled"]


class JobEnvelope(StrictBaseModel):
    """Validated job payload passed from the API to a runner metric."""

    job_id: str = Field(min_length=1, description="Stable job identifier.")
    metric: str = Field(min_length=1, description="Metric name selected by the client.")
    input: dict[str, Any] = Field(description="Validated metric input payload.")
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="Optional caller-provided idempotency key.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional job metadata passed through the runtime.",
    )


class JobEvent(StrictBaseModel):
    """Server-sent event emitted while a job moves through the queue."""

    job_id: str = Field(min_length=1, description="Job that emitted the event.")
    event: str = Field(min_length=1, description="Event name such as progress.")
    timestamp: datetime = Field(description="Event creation timestamp.")
    data: dict[str, Any] = Field(description="Event-specific JSON payload.")


class JobResult(StrictBaseModel):
    """Terminal result returned by a runner metric."""

    job_id: str = Field(min_length=1, description="Job that produced the result.")
    status: TerminalJobStatus = Field(description="Terminal job status.")
    result: Any | None = Field(default=None, description="JSON result payload.")
    result_type: str | None = Field(
        default=None,
        min_length=1,
        description="Optional result transport type, such as file.",
    )
    file_path: str | None = Field(
        default=None,
        min_length=1,
        description="Path to a file result written by the runner.",
    )
    error: dict[str, Any] | None = Field(
        default=None,
        description="Structured failure details for failed or cancelled jobs.",
    )


class JobCreateRequest(StrictBaseModel):
    """HTTP request body for submitting a job."""

    metric: str = Field(min_length=1, description="Metric name to execute.")
    input: dict[str, Any] = Field(description="Client payload for the selected metric.")
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="Optional caller-provided idempotency key.",
    )


class JobLinks(StrictBaseModel):
    """Convenience links returned with a newly queued job."""

    self: str = Field(min_length=1, description="URL for the job status resource.")
    events: str = Field(min_length=1, description="URL for the job event stream.")
    result: str = Field(min_length=1, description="URL for the terminal job result.")


class JobCreateResponse(StrictBaseModel):
    """HTTP response returned after a job is accepted."""

    job_id: str = Field(min_length=1, description="Identifier assigned to the job.")
    metric: str = Field(min_length=1, description="Metric accepted for execution.")
    status: Literal["queued"] = Field(description="Initial lifecycle status.")
    links: JobLinks = Field(description="Related job API URLs.")


class JobStatusInfo(StrictBaseModel):
    """Current status snapshot for a queued or completed job."""

    job_id: str = Field(min_length=1, description="Identifier assigned to the job.")
    status: JobLifecycleStatus = Field(description="Current lifecycle status.")
    updated_at: datetime = Field(description="Timestamp for the latest status update.")
    metric: str | None = Field(
        default=None,
        min_length=1,
        description="Metric associated with the job when available.",
    )
    error: dict[str, Any] | None = Field(
        default=None,
        description="Structured failure details when the job failed.",
    )


__all__ = [
    "JobCreateRequest",
    "JobCreateResponse",
    "JobEnvelope",
    "JobEvent",
    "JobLifecycleStatus",
    "JobLinks",
    "JobResult",
    "JobStatusInfo",
    "TerminalJobStatus",
]
