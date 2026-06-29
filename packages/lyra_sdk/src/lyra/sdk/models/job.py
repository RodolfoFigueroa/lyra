from datetime import datetime
from typing import Annotated, Any, Literal, Self

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field, TypeAdapter, model_validator

JobLifecycleStatus = Literal[
    "queued",
    "started",
    "progress",
    "succeeded",
    "failed",
    "cancelled",
]
TerminalJobStatus = Literal["succeeded", "failed", "cancelled"]


def _axis_values(values: Any) -> list[str]:
    return [str(value) for value in values]


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


class TableJobResult(StrictBaseModel):
    """Successful terminal result for metrics that return per-feature values."""

    kind: Literal["table"] = Field(default="table", description="Result kind.")
    job_id: str = Field(min_length=1, description="Job that produced the result.")
    status: Literal["succeeded"] = Field(
        default="succeeded",
        description="Terminal job status.",
    )
    index: list[str] = Field(
        min_length=1,
        description="Feature IDs matching the input GeoDataFrame index.",
    )
    columns: list[str] = Field(min_length=1, description="Table column names.")
    data: list[list[Any]] = Field(
        min_length=1,
        description="Row-major table values.",
    )

    @classmethod
    def from_dataframe(cls, job_id: str, dataframe: Any) -> Self:
        """Build a table result from a pandas-like DataFrame."""

        return cls(
            job_id=job_id,
            index=_axis_values(dataframe.index),
            columns=_axis_values(dataframe.columns),
            data=dataframe.to_numpy().tolist(),
        )

    @classmethod
    def from_series(
        cls,
        job_id: str,
        series: Any,
        *,
        name: str | None = None,
    ) -> Self:
        """Build a one-column table result from a pandas-like Series."""

        column_name = name or series.name or "value"
        return cls(
            job_id=job_id,
            index=_axis_values(series.index),
            columns=[str(column_name)],
            data=[[value] for value in series.tolist()],
        )

    @model_validator(mode="after")
    def validate_table_shape(self) -> Self:
        if len(self.index) != len(self.data):
            msg = "table index length must match data row count"
            raise ValueError(msg)

        width = len(self.columns)
        if any(len(row) != width for row in self.data):
            msg = "each table data row must match the column count"
            raise ValueError(msg)
        return self


class FileJobResult(StrictBaseModel):
    """Successful terminal result for metrics that produce a file artifact."""

    kind: Literal["file"] = Field(default="file", description="Result kind.")
    job_id: str = Field(min_length=1, description="Job that produced the result.")
    status: Literal["succeeded"] = Field(
        default="succeeded",
        description="Terminal job status.",
    )
    file_path: str = Field(
        min_length=1,
        description="Path to a file result written by the runner.",
    )
    media_type: str = Field(
        min_length=1,
        description="Media type for the produced file.",
    )


class FailedJobResult(StrictBaseModel):
    """Terminal result for failed jobs."""

    kind: Literal["failed"] = Field(default="failed", description="Result kind.")
    job_id: str = Field(min_length=1, description="Job that produced the result.")
    status: Literal["failed"] = Field(
        default="failed",
        description="Terminal job status.",
    )
    error: dict[str, Any] = Field(description="Structured failure details.")


class CancelledJobResult(StrictBaseModel):
    """Terminal result for cancelled jobs."""

    kind: Literal["cancelled"] = Field(
        default="cancelled",
        description="Result kind.",
    )
    job_id: str = Field(min_length=1, description="Job that produced the result.")
    status: Literal["cancelled"] = Field(
        default="cancelled",
        description="Terminal job status.",
    )
    error: dict[str, Any] | None = Field(
        default=None,
        description="Optional cancellation details.",
    )


TerminalJobResult = Annotated[
    TableJobResult | FileJobResult | FailedJobResult | CancelledJobResult,
    Field(discriminator="kind"),
]
_TERMINAL_JOB_RESULT_ADAPTER: TypeAdapter[TerminalJobResult] = TypeAdapter(
    TerminalJobResult
)


def parse_job_result(payload: Any) -> TerminalJobResult:
    """Parse a terminal job result payload into the discriminated result union."""

    return _TERMINAL_JOB_RESULT_ADAPTER.validate_python(payload)


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
    "CancelledJobResult",
    "FailedJobResult",
    "FileJobResult",
    "JobCreateRequest",
    "JobCreateResponse",
    "JobEnvelope",
    "JobEvent",
    "JobLifecycleStatus",
    "JobLinks",
    "JobStatusInfo",
    "TableJobResult",
    "TerminalJobResult",
    "TerminalJobStatus",
    "parse_job_result",
]
