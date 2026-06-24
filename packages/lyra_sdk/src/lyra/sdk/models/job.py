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
    job_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    input: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobEvent(StrictBaseModel):
    job_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    timestamp: datetime
    data: dict[str, Any]


class JobResult(StrictBaseModel):
    job_id: str = Field(min_length=1)
    status: TerminalJobStatus
    result: Any | None = None
    result_type: str | None = Field(default=None, min_length=1)
    file_path: str | None = Field(default=None, min_length=1)
    error: dict[str, Any] | None = None


class JobCreateRequest(StrictBaseModel):
    metric: str = Field(min_length=1)
    input: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1)


class JobLinks(StrictBaseModel):
    self: str = Field(min_length=1)
    events: str = Field(min_length=1)
    result: str = Field(min_length=1)


class JobCreateResponse(StrictBaseModel):
    job_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    status: Literal["queued"]
    links: JobLinks


class JobStatusInfo(StrictBaseModel):
    job_id: str = Field(min_length=1)
    status: JobLifecycleStatus
    updated_at: datetime
    metric: str | None = Field(default=None, min_length=1)
    error: dict[str, Any] | None = None


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
