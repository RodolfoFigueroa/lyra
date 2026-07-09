import math
from collections.abc import Iterable, Mapping, Sequence
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
ResultKind = Literal["table", "file", "failed", "cancelled"]
RawResultFormat = Literal["terminal_json", "jsonl"]

DEFAULT_RESULT_PREVIEW_ROWS = 20
DEFAULT_RESULT_INDEX_FIELD = "_result_index"


def _string_axis_values(values: Iterable[Any], *, axis: str) -> list[str]:
    string_values = [str(value) for value in values]
    if len(string_values) != len(set(string_values)):
        msg = f"table {axis} values must be unique after string conversion"
        raise ValueError(msg)
    return string_values


def _string_keyed_mapping(values: Mapping[Any, Any], *, axis: str) -> dict[str, Any]:
    string_keys = _string_axis_values(values.keys(), axis=axis)
    return dict(zip(string_keys, values.values(), strict=True))


def _is_sequence_values(values: Any) -> bool:
    return isinstance(values, Sequence) and not isinstance(
        values,
        str | bytes | bytearray,
    )


def _mapping_column_values(
    column: str,
    column_values: Mapping[Any, Any] | Sequence[Any],
    input_index: Sequence[Any],
) -> list[Any]:
    if isinstance(column_values, Mapping):
        try:
            return [column_values[feature_id] for feature_id in input_index]
        except KeyError as exc:
            msg = (
                f"values for column {column!r} are missing index value {exc.args[0]!r}"
            )
            raise ValueError(msg) from exc

    if not _is_sequence_values(column_values):
        msg = f"values for column {column!r} must be a mapping or sequence"
        raise ValueError(msg)

    if len(column_values) != len(input_index):
        msg = (
            f"values for column {column!r} must contain exactly "
            f"{len(input_index)} item(s)"
        )
        raise ValueError(msg)
    return list(column_values)


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
            index=_string_axis_values(dataframe.index, axis="index"),
            columns=_string_axis_values(dataframe.columns, axis="column"),
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
            index=_string_axis_values(series.index, axis="index"),
            columns=_string_axis_values([column_name], axis="column"),
            data=[[value] for value in series.tolist()],
        )

    @classmethod
    def from_mapping(
        cls,
        job_id: str,
        input_index: Iterable[Any],
        columns: Sequence[str],
        values: Mapping[str, Mapping[Any, Any] | Sequence[Any]],
    ) -> Self:
        """Build a table result from values keyed by the original input index."""

        raw_index = list(input_index)
        result_index = _string_axis_values(raw_index, axis="index")
        result_columns = _string_axis_values(columns, axis="column")
        values_by_column = _string_keyed_mapping(values, axis="column")

        expected_columns = set(result_columns)
        actual_columns = set(values_by_column)
        if actual_columns != expected_columns:
            missing = sorted(expected_columns - actual_columns)
            extra = sorted(actual_columns - expected_columns)
            details: list[str] = []
            if missing:
                details.append(f"missing column value(s): {', '.join(missing)}")
            if extra:
                details.append(f"unexpected column value(s): {', '.join(extra)}")
            msg = "; ".join(details)
            raise ValueError(msg)

        data_by_column = [
            _mapping_column_values(column, values_by_column[column], raw_index)
            for column in result_columns
        ]
        data = [list(row) for row in zip(*data_by_column, strict=True)]

        return cls(
            job_id=job_id,
            index=result_index,
            columns=result_columns,
            data=data,
        )

    @model_validator(mode="after")
    def validate_table_shape(self) -> Self:
        _string_axis_values(self.index, axis="index")
        _string_axis_values(self.columns, axis="column")

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


def result_ref_for_job(job_id: str) -> str:
    """Return the stable v1 result reference for a job."""

    return f"lyra://results/{job_id}"


class ResultReference(StrictBaseModel):
    """Stable agent-facing reference to a stored terminal job result."""

    job_id: str = Field(min_length=1, description="Job that produced the result.")
    uri: str = Field(min_length=1, description="Stable result reference URI.")

    @classmethod
    def for_job_id(cls, job_id: str) -> Self:
        return cls(job_id=job_id, uri=result_ref_for_job(job_id))

    @model_validator(mode="after")
    def validate_uri(self) -> Self:
        expected = result_ref_for_job(self.job_id)
        if self.uri != expected:
            msg = f"result reference must be {expected!r}"
            raise ValueError(msg)
        return self


class ResultLifetime(StrictBaseModel):
    """Redis-backed lifetime metadata for a stored result."""

    expires_in_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Approximate remaining result lifetime, when available.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Exact result expiry timestamp, when computable.",
    )


def _default_raw_result_formats() -> list[RawResultFormat]:
    return ["terminal_json"]


class ResultRawAccess(StrictBaseModel):
    """Metadata for retrieving the raw stored terminal result."""

    result_ref: str = Field(min_length=1, description="Stable result reference URI.")
    formats: list[RawResultFormat] = Field(
        default_factory=_default_raw_result_formats,
        min_length=1,
        description="Raw result formats supported by the server.",
    )
    terminal_json_path: str = Field(
        min_length=1,
        description="HTTP path for the stored terminal result JSON payload.",
    )
    jsonl_path: str | None = Field(
        default=None,
        min_length=1,
        description="HTTP path for a JSONL table export, when available.",
    )


class ResultTableMetadata(StrictBaseModel):
    """Shape metadata for a table terminal result."""

    row_count: int = Field(ge=0, description="Total number of rows in the table.")
    column_count: int = Field(ge=0, description="Total number of table columns.")
    columns: list[str] = Field(description="Ordered table column names.")
    index_field: str = Field(
        min_length=1,
        description="Preview field name containing the result index value.",
    )


class ResultTablePreview(StrictBaseModel):
    """Row-oriented preview of a table result."""

    index_field: str = Field(
        default=DEFAULT_RESULT_INDEX_FIELD,
        min_length=1,
        description="Field name containing the result index value.",
    )
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Preview rows as JSON objects.",
    )
    row_limit: int = Field(
        default=DEFAULT_RESULT_PREVIEW_ROWS,
        ge=0,
        description="Maximum number of preview rows requested.",
    )
    truncated: bool = Field(
        default=False,
        description="Whether the preview omits rows from the full table.",
    )


class NumericColumnSummary(StrictBaseModel):
    """Numeric statistics for one table column."""

    count: int = Field(ge=0, description="Number of finite numeric values.")
    null_count: int = Field(
        ge=0,
        description="Number of null or non-finite values.",
    )
    min: int | float | None = Field(default=None, description="Minimum value.")
    max: int | float | None = Field(default=None, description="Maximum value.")
    mean: float | None = Field(default=None, description="Arithmetic mean.")


class ResultColumnSummary(StrictBaseModel):
    """Summary statistics for one table column."""

    name: str = Field(min_length=1, description="Column name.")
    count: int = Field(ge=0, description="Number of non-null values.")
    null_count: int = Field(ge=0, description="Number of null values.")
    numeric: NumericColumnSummary | None = Field(
        default=None,
        description="Numeric statistics when every non-null value is numeric.",
    )


class ResultSummary(StrictBaseModel):
    """Agent-facing summary of the terminal result."""

    kind: ResultKind = Field(description="Stored terminal result kind.")
    row_count: int | None = Field(
        default=None,
        ge=0,
        description="Total rows for table results.",
    )
    column_count: int | None = Field(
        default=None,
        ge=0,
        description="Total columns for table results.",
    )
    columns: list[ResultColumnSummary] = Field(
        default_factory=list,
        description="Per-column summaries for table results.",
    )
    error: dict[str, Any] | None = Field(
        default=None,
        description="Terminal error details for failed or cancelled results.",
    )


class ResultFileMetadata(StrictBaseModel):
    """Metadata for a file terminal result."""

    file_path: str = Field(min_length=1, description="Stored file path.")
    media_type: str = Field(min_length=1, description="Stored file media type.")


class ResultDescriptor(StrictBaseModel):
    """Stable descriptor returned to agents instead of raw terminal payloads."""

    job_id: str = Field(min_length=1, description="Job that produced the result.")
    status: TerminalJobStatus = Field(description="Terminal job status.")
    result_kind: ResultKind = Field(description="Stored terminal result kind.")
    result_ref: str = Field(min_length=1, description="Stable result reference URI.")
    lifetime: ResultLifetime = Field(description="Redis-backed result lifetime.")
    raw: ResultRawAccess = Field(description="Raw terminal result access metadata.")
    table: ResultTableMetadata | None = Field(
        default=None,
        description="Table shape metadata for table results.",
    )
    preview: ResultTablePreview = Field(
        default_factory=ResultTablePreview,
        description="Row-oriented table preview.",
    )
    summary: ResultSummary = Field(description="Compact result summary.")
    file: ResultFileMetadata | None = Field(
        default=None,
        description="File metadata for file results.",
    )
    error: dict[str, Any] | None = Field(
        default=None,
        description="Terminal error details for failed or cancelled results.",
    )

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        expected = result_ref_for_job(self.job_id)
        if self.result_ref != expected:
            msg = f"result_ref must be {expected!r}"
            raise ValueError(msg)
        if self.raw.result_ref != self.result_ref:
            msg = "raw.result_ref must match result_ref"
            raise ValueError(msg)
        return self


def _preview_index_field(columns: Sequence[str]) -> str:
    field = DEFAULT_RESULT_INDEX_FIELD
    while field in columns:
        field = f"_{field}"
    return field


def build_table_preview(
    result: TableJobResult,
    *,
    row_limit: int = DEFAULT_RESULT_PREVIEW_ROWS,
    index_field: str | None = None,
) -> ResultTablePreview:
    """Build a row-oriented table preview that includes the result index."""

    preview_index_field = index_field or _preview_index_field(result.columns)
    rows: list[dict[str, Any]] = []
    for result_index, values in zip(
        result.index[:row_limit],
        result.data[:row_limit],
        strict=True,
    ):
        row = {preview_index_field: result_index}
        row.update(dict(zip(result.columns, values, strict=True)))
        rows.append(row)

    return ResultTablePreview(
        index_field=preview_index_field,
        rows=rows,
        row_limit=row_limit,
        truncated=len(result.data) > row_limit,
    )


def _is_nullish(value: Any) -> bool:
    return value is None or (isinstance(value, float) and not math.isfinite(value))


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _column_values(result: TableJobResult, column_position: int) -> list[Any]:
    return [row[column_position] for row in result.data]


def _numeric_summary(values: Sequence[Any]) -> NumericColumnSummary | None:
    null_count = sum(1 for value in values if _is_nullish(value))
    non_null_values = [value for value in values if not _is_nullish(value)]
    numeric_values = [value for value in non_null_values if _is_finite_number(value)]
    if not numeric_values or len(numeric_values) != len(non_null_values):
        return None

    return NumericColumnSummary(
        count=len(numeric_values),
        null_count=null_count,
        min=min(numeric_values),
        max=max(numeric_values),
        mean=sum(float(value) for value in numeric_values) / len(numeric_values),
    )


def build_table_summary(result: TableJobResult) -> ResultSummary:
    """Build deterministic per-column summaries for a table result."""

    columns: list[ResultColumnSummary] = []
    for position, column in enumerate(result.columns):
        values = _column_values(result, position)
        null_count = sum(1 for value in values if _is_nullish(value))
        columns.append(
            ResultColumnSummary(
                name=column,
                count=len(values) - null_count,
                null_count=null_count,
                numeric=_numeric_summary(values),
            )
        )

    return ResultSummary(
        kind="table",
        row_count=len(result.index),
        column_count=len(result.columns),
        columns=columns,
    )


def build_result_descriptor(
    result: TerminalJobResult,
    *,
    lifetime: ResultLifetime | None = None,
    terminal_json_path: str | None = None,
    jsonl_path: str | None = None,
    preview_row_limit: int = DEFAULT_RESULT_PREVIEW_ROWS,
) -> ResultDescriptor:
    """Build an agent-facing descriptor from a terminal result."""

    result_ref = result_ref_for_job(result.job_id)
    resolved_terminal_json_path = terminal_json_path or f"/jobs/{result.job_id}/result"
    resolved_lifetime = lifetime or ResultLifetime()

    if isinstance(result, TableJobResult):
        raw = ResultRawAccess(
            result_ref=result_ref,
            formats=["terminal_json", "jsonl"],
            terminal_json_path=resolved_terminal_json_path,
            jsonl_path=jsonl_path or f"/jobs/{result.job_id}/result/table.jsonl",
        )
        preview = build_table_preview(result, row_limit=preview_row_limit)
        return ResultDescriptor(
            job_id=result.job_id,
            status=result.status,
            result_kind=result.kind,
            result_ref=result_ref,
            lifetime=resolved_lifetime,
            raw=raw,
            table=ResultTableMetadata(
                row_count=len(result.index),
                column_count=len(result.columns),
                columns=result.columns,
                index_field=preview.index_field,
            ),
            preview=preview,
            summary=build_table_summary(result),
        )

    raw = ResultRawAccess(
        result_ref=result_ref,
        terminal_json_path=resolved_terminal_json_path,
    )

    if isinstance(result, FileJobResult):
        file_metadata = ResultFileMetadata(
            file_path=result.file_path,
            media_type=result.media_type,
        )
        return ResultDescriptor(
            job_id=result.job_id,
            status=result.status,
            result_kind=result.kind,
            result_ref=result_ref,
            lifetime=resolved_lifetime,
            raw=raw,
            summary=ResultSummary(kind="file"),
            file=file_metadata,
        )

    error = result.error
    return ResultDescriptor(
        job_id=result.job_id,
        status=result.status,
        result_kind=result.kind,
        result_ref=result_ref,
        lifetime=resolved_lifetime,
        raw=raw,
        summary=ResultSummary(kind=result.kind, error=error),
        error=error,
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


class JobListResponse(StrictBaseModel):
    """Admin response containing recent job status snapshots."""

    jobs: list[JobStatusInfo] = Field(description="Recent jobs ordered newest-first.")


class JobCancelResponse(StrictBaseModel):
    """Admin response returned after a job cancellation request."""

    job_id: str = Field(min_length=1, description="Job that was cancelled.")
    status: Literal["cancelled"] = Field(description="Status after cancellation.")
    cancellation_requested: bool = Field(
        description="Whether Lyra accepted the cancellation request.",
    )
    revoke_requested: bool = Field(
        description="Whether Lyra attempted to revoke the Celery task.",
    )


__all__ = [
    "DEFAULT_RESULT_INDEX_FIELD",
    "DEFAULT_RESULT_PREVIEW_ROWS",
    "CancelledJobResult",
    "FailedJobResult",
    "FileJobResult",
    "JobCancelResponse",
    "JobCreateRequest",
    "JobCreateResponse",
    "JobEnvelope",
    "JobEvent",
    "JobLifecycleStatus",
    "JobLinks",
    "JobListResponse",
    "JobStatusInfo",
    "NumericColumnSummary",
    "RawResultFormat",
    "ResultColumnSummary",
    "ResultDescriptor",
    "ResultFileMetadata",
    "ResultKind",
    "ResultLifetime",
    "ResultRawAccess",
    "ResultReference",
    "ResultSummary",
    "ResultTableMetadata",
    "ResultTablePreview",
    "TableJobResult",
    "TerminalJobResult",
    "TerminalJobStatus",
    "build_result_descriptor",
    "build_table_preview",
    "build_table_summary",
    "parse_job_result",
    "result_ref_for_job",
]
