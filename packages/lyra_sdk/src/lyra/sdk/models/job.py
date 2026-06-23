from datetime import datetime
from typing import Any, Literal

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


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
    status: Literal["succeeded", "failed", "cancelled"]
    result: Any | None = None
    result_type: str | None = Field(default=None, min_length=1)
    file_path: str | None = Field(default=None, min_length=1)
    error: dict[str, Any] | None = None


__all__ = ["JobEnvelope", "JobEvent", "JobResult"]
