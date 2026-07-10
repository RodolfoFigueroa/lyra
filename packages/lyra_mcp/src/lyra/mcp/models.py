from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

MAX_RUN_WAIT_SECONDS = 10.0
MAX_RESULT_WAIT_SECONDS = 30.0
MAX_METRIC_PAGE_SIZE = 20
RESULT_REF_PATTERN = r"^lyra://results/[^/?#\s]+$"


class MCPContractModel(BaseModel):
    """Strict base for agent-facing MCP contracts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class SearchMetricsInput(MCPContractModel):
    query: str = Field(
        min_length=1,
        description=(
            "Meaningful task-specific words to match against metric names, "
            "descriptions, inputs, and outputs. Do not use empty, single-letter, "
            "or generic inventory queries."
        ),
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=MAX_METRIC_PAGE_SIZE,
        description=(
            "Maximum candidates to return. Usually omit this field; maximum 20."
        ),
    )


class ListMetricsInput(MCPContractModel):
    cursor: str | None = Field(
        default=None,
        min_length=1,
        description="Opaque continuation cursor from the preceding list response.",
    )
    limit: int = Field(
        default=MAX_METRIC_PAGE_SIZE,
        ge=1,
        le=MAX_METRIC_PAGE_SIZE,
        description="Maximum catalog entries to return. Usually omit; maximum 20.",
    )


class LookupMetZoneInput(MCPContractModel):
    name: str = Field(
        min_length=1,
        description=(
            "Natural-language metropolitan-zone name, including supported "
            "misspellings, to resolve through Lyra's public fuzzy lookup."
        ),
    )


class GetMetricInput(MCPContractModel):
    metric: str = Field(min_length=1, description="Public metric name.")


class RunMetricInput(MCPContractModel):
    metric: str = Field(min_length=1, description="Public metric name.")
    met_zone_code: str = Field(
        min_length=1,
        description="Raw metropolitan zone code supplied to the metric.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-spatial metric input values.",
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="Caller-provided key for safely retrying this metric submission.",
    )
    wait_seconds: float = Field(
        default=2,
        ge=0,
        le=MAX_RUN_WAIT_SECONDS,
        allow_inf_nan=False,
        description="Maximum time to wait for a terminal result.",
    )


class ResultRefInput(MCPContractModel):
    result_ref: str = Field(
        pattern=RESULT_REF_PATTERN,
        description="Stable reference shaped lyra://results/{job_id}.",
    )


class GetJobResultInput(ResultRefInput):
    wait_seconds: float = Field(
        default=MAX_RESULT_WAIT_SECONDS,
        ge=0,
        le=MAX_RESULT_WAIT_SECONDS,
        allow_inf_nan=False,
        description="Maximum time to wait for a terminal result.",
    )


class OutputColumn(MCPContractModel):
    name: str = Field(min_length=1)
    type: Literal["number", "integer", "string", "boolean"]
    unit: str = Field(min_length=1)
    description: str = Field(min_length=1)
    nullable: bool
    source: str | None = Field(default=None, min_length=1)


class SpatialField(MCPContractModel):
    field: str = Field(min_length=1)
    kind: Literal["location", "bounds"]


class SearchCandidate(MCPContractModel):
    metric: str = Field(min_length=1)
    description: str
    reason: str = Field(min_length=1)
    required_spatial_fields: list[SpatialField]
    output_kind: Literal["table", "file"]
    relevant_columns: list[OutputColumn]


class SearchMetricsOutput(MCPContractModel):
    query: str
    catalog_fingerprint: str | None
    candidates: list[SearchCandidate]


class MetricListItem(MCPContractModel):
    name: str = Field(min_length=1)
    description: str


class ListMetricsOutput(MCPContractModel):
    catalog_fingerprint: str = Field(min_length=1)
    total_count: int = Field(ge=0)
    metrics: list[MetricListItem]
    next_cursor: str | None = Field(default=None, min_length=1)


class LookupMetZoneOutput(MCPContractModel):
    cve_met: str = Field(
        min_length=1,
        description="Canonical metropolitan-zone code accepted by Lyra metrics.",
    )
    nom_met: str = Field(
        min_length=1,
        description="Canonical display name matched by the public fuzzy lookup.",
    )


class TableMetricOutput(MCPContractModel):
    kind: Literal["table"]
    columns: list[OutputColumn]
    batched_columns: list[OutputColumn]


class FileMetricOutput(MCPContractModel):
    kind: Literal["file"]
    media_type: str = Field(min_length=1)
    extensions: list[str]


MetricOutput = Annotated[
    TableMetricOutput | FileMetricOutput,
    Field(discriminator="kind"),
]


class GetMetricOutput(MCPContractModel):
    name: str = Field(min_length=1)
    description: str
    request_schema: dict[str, Any]
    spatial_inputs: dict[str, Literal["location", "bounds"]]
    output: MetricOutput


class RunningOutput(MCPContractModel):
    status: Literal["running"]
    job_id: str = Field(min_length=1)
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    poll_after_seconds: int = Field(ge=0)
    next_tool: Literal["lyra_get_job_result"]


class RunMetricRunningOutput(RunningOutput):
    reused: bool


class ResultLifetimeOutput(MCPContractModel):
    expires_in_seconds: int | None = Field(default=None, ge=0)
    expires_at: str | None = None


class ResultRawAccessOutput(MCPContractModel):
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    formats: list[Literal["terminal_json", "jsonl"]]
    terminal_json_path: str = Field(min_length=1)
    jsonl_path: str | None = Field(default=None, min_length=1)


class PluginInfoOutput(MCPContractModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class RowIdentityOutput(MCPContractModel):
    field: str = Field(min_length=1)
    namespace: str | None = Field(default=None, min_length=1)
    version: str | None = Field(default=None, min_length=1)


class JobRunProvenanceOutput(MCPContractModel):
    metric: str = Field(min_length=1)
    catalog_fingerprint: str = Field(min_length=1)
    plugin: PluginInfoOutput
    input: dict[str, Any]
    output: MetricOutput
    created_at: str = Field(min_length=1)
    row_identity: RowIdentityOutput | None = None


class ResultTableMetadataOutput(MCPContractModel):
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[str]
    column_contracts: list[OutputColumn]
    index_field: str = Field(min_length=1)
    row_identity: RowIdentityOutput | None = None


class ResultTablePreviewOutput(MCPContractModel):
    index_field: str = Field(min_length=1)
    rows: list[dict[str, Any]]
    row_limit: int = Field(ge=0)
    truncated: bool


class NumericColumnSummaryOutput(MCPContractModel):
    count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    min: int | float | None = None
    max: int | float | None = None
    mean: float | None = None


class ResultColumnSummaryOutput(MCPContractModel):
    name: str = Field(min_length=1)
    count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    numeric: NumericColumnSummaryOutput | None = None


class ResultSummaryOutput(MCPContractModel):
    kind: Literal["table", "file", "failed", "cancelled"]
    row_count: int | None = Field(default=None, ge=0)
    column_count: int | None = Field(default=None, ge=0)
    columns: list[ResultColumnSummaryOutput]
    error: dict[str, Any] | None = None


class ResultFileMetadataOutput(MCPContractModel):
    file_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)


class ResultDescriptorOutput(MCPContractModel):
    schema_version: Literal[1]
    job_id: str = Field(min_length=1)
    status: Literal["succeeded", "failed", "cancelled"]
    result_kind: Literal["table", "file", "failed", "cancelled"]
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    provenance: JobRunProvenanceOutput | None = None
    completed_at: str = Field(min_length=1)
    lifetime: ResultLifetimeOutput
    raw: ResultRawAccessOutput
    table: ResultTableMetadataOutput | None = None
    preview: ResultTablePreviewOutput
    summary: ResultSummaryOutput
    file: ResultFileMetadataOutput | None = None
    error: dict[str, Any] | None = None


class RunMetricResultDescriptorOutput(ResultDescriptorOutput):
    reused: bool


RunMetricOutput = RunMetricRunningOutput | RunMetricResultDescriptorOutput
GetJobResultOutput = RunningOutput | ResultDescriptorOutput


class ResultMetadataOutput(MCPContractModel):
    schema_version: Literal[1]
    job_id: str = Field(min_length=1)
    status: Literal["succeeded", "failed", "cancelled"]
    result_kind: Literal["table", "file", "failed", "cancelled"]
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    provenance: JobRunProvenanceOutput | None
    completed_at: str = Field(min_length=1)
    lifetime: ResultLifetimeOutput
    table: ResultTableMetadataOutput | None
    file: ResultFileMetadataOutput | None
    summary: ResultSummaryOutput
    error: dict[str, Any] | None


class ResultPreviewOutput(MCPContractModel):
    schema_version: Literal[1]
    job_id: str = Field(min_length=1)
    status: Literal["succeeded", "failed", "cancelled"]
    result_kind: Literal["table", "file", "failed", "cancelled"]
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    provenance: JobRunProvenanceOutput | None
    completed_at: str = Field(min_length=1)
    lifetime: ResultLifetimeOutput
    preview: ResultTablePreviewOutput
    summary: ResultSummaryOutput
    error: dict[str, Any] | None


class BearerAuthenticationOutput(MCPContractModel):
    scheme: Literal["Bearer"]
    credential_env_var: Literal["LYRA_AGENT_API_KEY"]


class LyraAPIHandoffOutput(MCPContractModel):
    method: Literal["GET"]
    url: str = Field(pattern=r"^https?://[^/?#]+(?:/[^?#]*)?$")
    authentication: BearerAuthenticationOutput


class ClientHelpersOutput(MCPContractModel):
    python_sync: str = Field(min_length=1)
    python_async: str = Field(min_length=1)


class DownloadResultOutput(MCPContractModel):
    job_id: str = Field(min_length=1)
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    status: Literal["succeeded"]
    format: Literal["jsonl"]
    media_type: Literal["application/x-ndjson"]
    lyra_api: LyraAPIHandoffOutput
    client_helpers: ClientHelpersOutput
    expires_in_seconds: int | None = Field(default=None, ge=0)
    expires_at: str | None = None


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    input_model: type[MCPContractModel]
    output_adapter: TypeAdapter[Any]
    read_only: bool
    idempotent: bool
    open_world: bool

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        return self.output_adapter.json_schema()


def _contract(
    name: str,
    description: str,
    input_model: type[MCPContractModel],
    output_type: Any,
    *,
    read_only: bool,
    idempotent: bool,
    open_world: bool = False,
) -> ToolContract:
    return ToolContract(
        name=name,
        description=description,
        input_model=input_model,
        output_adapter=TypeAdapter(output_type),
        read_only=read_only,
        idempotent=idempotent,
        open_world=open_world,
    )


TOOL_CONTRACTS = (
    _contract(
        "lyra_lookup_met_zone",
        (
            "Resolve a natural-language metropolitan-zone name or supported "
            "misspelling to the canonical cve_met code and matched nom_met display "
            "name. Use cve_met as met_zone_code when calling lyra_run_metric."
        ),
        LookupMetZoneInput,
        LookupMetZoneOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_list_metrics",
        (
            "List a compact, paginated inventory of Lyra's public metric catalog. "
            "Use only when the user explicitly asks which or all metrics are "
            "available, or after focused searches return no candidates. Do not "
            "use this for ordinary task-specific metric selection; use "
            "lyra_search_metrics instead."
        ),
        ListMetricsInput,
        ListMetricsOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_search_metrics",
        (
            "Search Lyra's public metric catalog for task-specific metric "
            "selection. Use meaningful task terms rather than empty, single-letter, "
            "broad, or inventory queries. Returns candidate reasons, required "
            "spatial fields, output kind, and relevant output columns."
        ),
        SearchMetricsInput,
        SearchMetricsOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_get_metric",
        (
            "Return the public contract for one Lyra metric, including its request "
            "schema, spatial input metadata, and declared output."
        ),
        GetMetricInput,
        GetMetricOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_run_metric",
        (
            "Start one Lyra metric for a raw metropolitan zone code. Pass "
            "non-spatial inputs in parameters. If the response has "
            "status='running', do not rerun the metric; wait poll_after_seconds "
            "and call lyra_get_job_result, the returned next_tool, with the "
            "returned result_ref."
        ),
        RunMetricInput,
        RunMetricOutput,
        read_only=False,
        idempotent=False,
        open_world=True,
    ),
    _contract(
        "lyra_get_job_result",
        (
            "Continue polling a Lyra result reference. Returns status='running' "
            "with next_tool='lyra_get_job_result' while the job is active, or the "
            "compact terminal descriptor for succeeded, failed, or cancelled jobs. "
            "Expired references return a structured error telling the agent to "
            "rerun the job if the user still needs data."
        ),
        GetJobResultInput,
        GetJobResultOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_get_result_metadata",
        (
            "Return compact descriptor metadata for a Lyra result reference without "
            "hydrating the raw table."
        ),
        ResultRefInput,
        ResultMetadataOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_get_result_preview",
        (
            "Return only the descriptor preview rows and summary for a Lyra result "
            "reference."
        ),
        ResultRefInput,
        ResultPreviewOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_download_result",
        (
            "Return authenticated Lyra API handoff metadata for downloading a table "
            "result as JSONL. This does not inline raw rows."
        ),
        ResultRefInput,
        DownloadResultOutput,
        read_only=True,
        idempotent=True,
    ),
)

TOOL_CONTRACTS_BY_NAME = {contract.name: contract for contract in TOOL_CONTRACTS}


__all__ = [
    "MAX_METRIC_PAGE_SIZE",
    "MAX_RESULT_WAIT_SECONDS",
    "MAX_RUN_WAIT_SECONDS",
    "TOOL_CONTRACTS",
    "TOOL_CONTRACTS_BY_NAME",
    "MCPContractModel",
    "ToolContract",
]
