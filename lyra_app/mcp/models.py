"""Data models exposed through Lyra's MCP tools."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict, Unpack

from lyra.sdk.models.job import (
    JobProgress,
    ResultLifetime,
    ResultSummary,
    ResultTableMetadata,
    ResultTablePreview,
    RowIdentityMetadata,
)
from lyra.sdk.models.plugin import (
    OutputSpec,
    PluginInfo,
    TableColumn,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from typing_extensions import TypeForm

MAX_METRIC_PAGE_SIZE = 20
RESULT_REF_PATTERN = r"^lyra://results/[^/?#\s]+$"


class MCPContractModel(BaseModel):
    """Strict base for the application's agent-facing MCP contracts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class SearchMetricsInput(MCPContractModel):
    """Search the metric catalog with a bounded lexical query."""

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
    """Select a page of the public metric catalog."""

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
    """Resolve a metropolitan zone from a human-readable name."""

    name: str = Field(
        min_length=1,
        description=(
            "Natural-language metropolitan-zone name, including supported "
            "misspellings, to resolve through Lyra's public fuzzy lookup."
        ),
    )


class GetMetricInput(MCPContractModel):
    """Select one metric by its public name."""

    metric: str = Field(min_length=1, description="Public metric name.")


class RunMetricInput(MCPContractModel):
    """Submit a metric once with spatial inputs."""

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


class ResultRefInput(MCPContractModel):
    """Identify a retained result by its stable Lyra reference."""

    result_ref: str = Field(
        pattern=RESULT_REF_PATTERN,
        description="Stable reference shaped lyra://results/{job_id}.",
    )


class GetJobResultInput(ResultRefInput):
    """Observe one result without waiting for completion."""


class SpatialField(MCPContractModel):
    """Identify a spatial request field and its input kind."""

    field: str = Field(min_length=1)
    kind: Literal["location", "bounds"]


class SearchCandidate(MCPContractModel):
    """Describe a metric matched by a catalog search."""

    metric: str = Field(min_length=1)
    description: str
    reason: str = Field(min_length=1)
    required_spatial_fields: list[SpatialField]
    output_kind: Literal["table", "file"]
    relevant_columns: list[TableColumn]


class SearchMetricsOutput(MCPContractModel):
    """Return ranked metric candidates and catalog identity."""

    query: str
    catalog_fingerprint: str | None
    candidates: list[SearchCandidate]


class MetricListItem(MCPContractModel):
    """Summarize one metric in a catalog page."""

    name: str = Field(min_length=1)
    description: str


class ListMetricsOutput(MCPContractModel):
    """Return a catalog page and its continuation cursor."""

    catalog_fingerprint: str = Field(min_length=1)
    total_count: int = Field(ge=0)
    metrics: list[MetricListItem]
    next_cursor: str | None = Field(default=None, min_length=1)


class LookupMetZoneOutput(MCPContractModel):
    """Return the canonical metropolitan-zone code and name."""

    cve_met: str = Field(
        min_length=1,
        description="Canonical metropolitan-zone code accepted by Lyra metrics.",
    )
    nom_met: str = Field(
        min_length=1,
        description="Canonical display name matched by the public fuzzy lookup.",
    )


class GetMetricOutput(MCPContractModel):
    """Return a metric request schema and output contract."""

    name: str = Field(min_length=1)
    description: str
    request_schema: dict[str, Any]
    spatial_inputs: dict[str, Literal["location", "bounds"]]
    output: OutputSpec


class RunMetricOutput(MCPContractModel):
    """Acknowledge submission without assuming execution status."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    reused: bool
    next_tool: Literal["lyra_get_job_result"] = "lyra_get_job_result"


class TruncationOutput(MCPContractModel):
    """Record all omissions from a bounded inspection."""

    omitted_rows: int = Field(default=0, ge=0)
    omitted_columns: int = Field(default=0, ge=0)
    shortened_strings: int = Field(default=0, ge=0)
    omitted_sections: list[str] = Field(default_factory=list)


class CompactProvenance(MCPContractModel):
    """Retain run identity without geometry or full output declarations."""

    metric: str
    catalog_fingerprint: str
    plugin: PluginInfo
    created_at: datetime
    row_identity: RowIdentityMetadata | None = None
    input: dict[str, Any] | None = None


class ActiveResultOutput(MCPContractModel):
    """Report the actual retained active state."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    status: Literal["queued", "running"]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    metric: str | None = None
    progress: JobProgress | None = None
    poll_after_seconds: Literal[2] = 2
    next_tool: Literal["lyra_get_job_result"] = "lyra_get_job_result"
    truncation: TruncationOutput = Field(default_factory=TruncationOutput)


class BearerAuthenticationOutput(MCPContractModel):
    """Describe the bearer credential needed for an API handoff."""

    scheme: Literal["Bearer"]
    credential_env_var: Literal["LYRA_AGENT_API_KEY"]


class LyraAPIHandoffOutput(MCPContractModel):
    """Describe an authenticated HTTP result download."""

    method: Literal["GET"]
    url: str = Field(pattern=r"^https?://[^/?#]+(?:/[^?#]*)?$")
    authentication: BearerAuthenticationOutput


class ResultFileOutput(MCPContractModel):
    """Expose file media type and authenticated download access."""

    media_type: str
    download: LyraAPIHandoffOutput


class TerminalResultOutput(MCPContractModel):
    """A compact terminal observation with complete-descriptor access."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    status: Literal["succeeded", "failed", "cancelled"]
    result_kind: Literal["table", "file", "failed", "cancelled"]
    completed_at: datetime | None = None
    lifetime: ResultLifetime
    descriptor: LyraAPIHandoffOutput
    provenance: CompactProvenance | None = None
    table: ResultTableMetadata | None = None
    preview: ResultTablePreview | None = None
    summary: ResultSummary | None = None
    file: ResultFileOutput | None = None
    error: dict[str, Any] | None = None
    truncation: TruncationOutput = Field(default_factory=TruncationOutput)


GetJobResultOutput = ActiveResultOutput | TerminalResultOutput


class DownloadResultOutput(MCPContractModel):
    """Return authenticated download instructions and result lifetime."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    status: Literal["succeeded"] = "succeeded"
    format: Literal["jsonl", "file"]
    media_type: str
    lifetime: ResultLifetime
    lyra_api: LyraAPIHandoffOutput


@dataclass(frozen=True)
class ToolContract:
    """Describe one MCP tool's schemas and behavioral annotations."""

    name: str
    description: str
    input_model: type[MCPContractModel]
    output_adapter: TypeAdapter[BaseModel]
    read_only: bool
    idempotent: bool
    open_world: bool

    @property
    def input_schema(self) -> dict[str, Any]:
        """The JSON Schema generated from the strict input contract."""
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        """The JSON Schema generated for the complete output union."""
        return self.output_adapter.json_schema(mode="serialization")


class ToolBehavior(TypedDict):
    """Behavioral annotations advertised for an MCP tool."""

    read_only: bool
    idempotent: bool
    open_world: NotRequired[bool]


def _contract(
    name: str,
    description: str,
    input_model: type[MCPContractModel],
    output_type: TypeForm[BaseModel],
    **behavior: Unpack[ToolBehavior],
) -> ToolContract:
    return ToolContract(
        name=name,
        description=description,
        input_model=input_model,
        output_adapter=TypeAdapter(output_type),
        read_only=behavior["read_only"],
        idempotent=behavior["idempotent"],
        open_world=behavior.get("open_world", False),
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
            "Submit once, then inspect result_ref with lyra_get_job_result. "
            "reused means idempotent replay, not caching or completion. "
            "On submission timeout reuse the original idempotency key."
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
            "Inspect once. Poll active queued/running jobs after two seconds. "
            "Terminal failures are observations. Preview limits: 10 rows, 20 data "
            "columns, 500 characters per display string, 64 KiB per response. "
            "Use descriptor access for complete provenance; unknown or expired "
            "references return result_not_found."
        ),
        GetJobResultInput,
        GetJobResultOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_download_result",
        (
            "Return authenticated Lyra API handoff metadata for downloading a table "
            "or file result. This does not inline raw data."
        ),
        ResultRefInput,
        DownloadResultOutput,
        read_only=True,
        idempotent=True,
    ),
)

TOOL_CONTRACTS_BY_NAME = {contract.name: contract for contract in TOOL_CONTRACTS}
