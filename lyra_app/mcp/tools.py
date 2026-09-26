"""MCP tool implementations for querying and operating Lyra."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast

from anyio import Path
from lyra.sdk.models.job import (
    FileJobResult,
    JobCreateRequest,
    JobCreateResponse,
    TableJobResult,
)
from lyra.sdk.models.metric import (
    MetricCatalogResponse,
    MetricInfo,
    normalize_metric_search_tokens,
)
from lyra.sdk.types import JsonObject, JsonValue, validate_json_value
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from typing_extensions import override

from lyra_app.db.connection import (
    ApplicationDatabaseRuntime,
    DatabaseUnavailableError,
    is_database_unavailable_error,
)
from lyra_app.job_observation import JobObservation, observe_job
from lyra_app.job_submission import (
    SubmissionUnavailableError,
    UnknownMetricError,
    submit_job,
)
from lyra_app.loaders.db import get_met_zone_code_from_name_async
from lyra_app.mcp.models import (
    TOOL_CONTRACTS_BY_NAME,
    DownloadResultOutput,
    RunMetricOutput,
)
from lyra_app.mcp.results import handoff, project_observation
from lyra_app.registry import (
    CatalogUnavailableError,
    MetricPayloadValidationError,
    get_metric_catalog,
    get_metric_info,
)
from lyra_app.spatial_inputs import (
    SpatialInputResolutionUnavailableError,
    SpatialInputValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lyra_app.mcp.models import (
        GetJobResultInput,
        GetMetricInput,
        ListMetricsInput,
        LookupMetZoneInput,
        MCPContractModel,
        ResultRefInput,
        RunMetricInput,
        SearchMetricsInput,
    )

OPERATION_TIMEOUT_SECONDS = 30.0
_RESULT_REF_PATTERN = re.compile(r"^lyra://results/([^/?#\s]+)$")
_UNKNOWN_METRIC_ERROR = "unknown_metric"
_INVALID_PARAMETERS_ERROR = "invalid_parameters"
_BACKEND_ERROR = "backend_error"
_DATABASE_UNAVAILABLE_ERROR = "database_unavailable"
_METRIC_CURSOR_VERSION = 1
_MAX_COMPACT_DESCRIPTION_LENGTH = 240


class LyraMCPBackend(Protocol):
    """Define the domain operations used by transport-independent MCP tools."""

    async def get_metrics(self) -> MetricCatalogResponse:
        """Return the complete public metric catalog."""
        ...

    async def lookup_met_zone(self, name: str) -> dict[str, str] | None:
        """Resolve a metropolitan-zone name to its canonical code and name."""
        ...

    async def get_metric(self, metric: str) -> MetricInfo | None:
        """Return public metadata for a metric when it exists."""
        ...

    async def create_job(
        self,
        metric: str,
        payload: JsonObject,
    ) -> JobCreateResponse:
        """Validate and submit a metric job through the Lyra domain service."""
        ...

    async def observe_job(self, job_id: str) -> JobObservation:
        """Read native lifecycle state and best-effort retained metadata."""
        ...


@dataclass(frozen=True)
class ToolCallError(Exception):
    """Transport-independent domain failure returned as a structured tool error."""

    code: str
    message: str
    details: JsonValue = None

    def to_payload(self) -> dict[str, Any]:
        """Render this failure using the MCP tool error envelope.

        Returns:
            A structured error object with optional domain-specific details.
        """
        error: JsonObject = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


_CATALOG_UNAVAILABLE_ERROR = "catalog_unavailable"


class InProcessLyraBackend(LyraMCPBackend):
    """Implement MCP domain operations directly against this Lyra process."""

    def __init__(self, database: ApplicationDatabaseRuntime) -> None:
        """Initialize the backend with the application database runtime."""
        self.database = database

    @override
    async def get_metrics(self) -> MetricCatalogResponse:
        try:
            return await asyncio.to_thread(get_metric_catalog)
        except CatalogUnavailableError as exc:
            raise ToolCallError(
                _CATALOG_UNAVAILABLE_ERROR,
                "Plugin catalog is unavailable.",
                {"retryable": True},
            ) from exc

    @override
    async def lookup_met_zone(self, name: str) -> dict[str, str] | None:
        try:
            async with self.database.require_async_engine().connect() as connection:
                result = await get_met_zone_code_from_name_async(
                    name,
                    conn=connection,
                )
        except SQLAlchemyError as exc:
            if not is_database_unavailable_error(exc):
                raise
            raise ToolCallError(
                _DATABASE_UNAVAILABLE_ERROR,
                "The spatial database is temporarily unavailable.",
                {
                    "retryable": True,
                    "retry_after_seconds": (
                        self.database.config.database.retry_after_seconds
                    ),
                },
            ) from exc
        if result is None:
            return None
        cve_met, nom_met = result
        return {"cve_met": cve_met, "nom_met": nom_met}

    @override
    async def get_metric(self, metric: str) -> MetricInfo | None:
        try:
            return await asyncio.to_thread(get_metric_info, metric)
        except CatalogUnavailableError as exc:
            raise ToolCallError(
                _CATALOG_UNAVAILABLE_ERROR,
                "Plugin catalog is unavailable.",
                {"retryable": True},
            ) from exc

    @override
    async def create_job(
        self,
        metric: str,
        payload: JsonObject,
    ) -> JobCreateResponse:
        try:
            return await submit_job(
                JobCreateRequest(
                    metric=metric,
                    input=payload,
                ),
                database=self.database,
            )
        except CatalogUnavailableError as exc:
            raise ToolCallError(
                _CATALOG_UNAVAILABLE_ERROR,
                "Plugin catalog is unavailable.",
                {"retryable": True},
            ) from exc
        except UnknownMetricError as exc:
            raise ToolCallError(_UNKNOWN_METRIC_ERROR, str(exc)) from exc
        except (MetricPayloadValidationError, SpatialInputValidationError) as exc:
            raise ToolCallError(
                _INVALID_PARAMETERS_ERROR,
                "Invalid metric parameters.",
                validate_json_value(exc.errors),
            ) from exc
        except (
            DatabaseUnavailableError,
            SpatialInputResolutionUnavailableError,
            SubmissionUnavailableError,
        ) as exc:
            if isinstance(
                exc,
                DatabaseUnavailableError | SpatialInputResolutionUnavailableError,
            ):
                retry_after = self.database.config.database.retry_after_seconds
                raise ToolCallError(
                    _DATABASE_UNAVAILABLE_ERROR,
                    "The spatial database is temporarily unavailable.",
                    {"retryable": True, "retry_after_seconds": retry_after},
                ) from exc
            raise ToolCallError(
                _BACKEND_ERROR,
                (
                    "Submission could not be confirmed. "
                    "The job may have been accepted; "
                    "another submission may duplicate it."
                ),
                {"retryable": True},
            ) from exc

    @override
    async def observe_job(self, job_id: str) -> JobObservation:
        try:
            return await observe_job(job_id)
        except (ValueError, TypeError) as exc:
            code = "result_observation_error"
            message = "The retained job state could not be decoded."
            raise ToolCallError(code, message) from exc


async def execute_tool(
    name: str,
    arguments: MCPContractModel,
    backend: LyraMCPBackend,
    *,
    public_api_base_url: str,
) -> BaseModel:
    """Execute once under a fixed job-operation deadline and validate the output.

    Returns:
        A typed output ready for serialization at the transport boundary.

    Raises:
        ToolCallError: For domain, timeout, or infrastructure failures.
    """
    try:
        timeout = (
            OPERATION_TIMEOUT_SECONDS
            if name
            in {"lyra_run_metric", "lyra_get_job_result", "lyra_download_result"}
            else None
        )
        async with asyncio.timeout(timeout):
            payload = await _execute_tool(
                name, arguments, backend, public_api_base_url=public_api_base_url
            )
        return TOOL_CONTRACTS_BY_NAME[name].output_adapter.validate_python(payload)
    except TimeoutError as exc:
        action = (
            "Retry observation."
            if name != "lyra_run_metric"
            else ("Submission may have succeeded; another submission may duplicate it.")
        )
        code = "operation_timeout"
        message = "The backend operation exceeded 30 seconds."
        raise ToolCallError(
            code,
            message,
            {"retryable": True, "action": action},
        ) from exc
    except (RedisError, OSError) as exc:
        code = "backend_error"
        message = (
            "Submission may have been accepted; another submission may duplicate it."
            if name == "lyra_run_metric"
            else "The result backend is temporarily unavailable."
        )
        raise ToolCallError(
            code,
            message,
            {"retryable": True},
        ) from exc


async def _execute_tool(
    name: str,
    arguments: MCPContractModel,
    backend: LyraMCPBackend,
    *,
    public_api_base_url: str,
) -> BaseModel | dict[str, Any]:
    """Execute one validated tool call against the Lyra domain service.

    Returns:
        The tool-specific success payload.

    Raises:
        ToolCallError: If the requested tool name is unknown.
    """
    if name == "lyra_lookup_met_zone":
        payload = await _lookup_met_zone(cast("LookupMetZoneInput", arguments), backend)
    elif name == "lyra_list_metrics":
        payload = await _list_metrics(cast("ListMetricsInput", arguments), backend)
    elif name == "lyra_search_metrics":
        payload = await _search_metrics(cast("SearchMetricsInput", arguments), backend)
    elif name == "lyra_get_metric":
        payload = await _get_metric(cast("GetMetricInput", arguments), backend)
    elif name == "lyra_run_metric":
        payload = await _run_metric(cast("RunMetricInput", arguments), backend)
    elif name == "lyra_get_job_result":
        payload = await _get_job_result(
            cast("GetJobResultInput", arguments), backend, public_api_base_url
        )
    elif name == "lyra_download_result":
        payload = await _download_result(
            cast("ResultRefInput", arguments),
            backend,
            public_api_base_url=public_api_base_url,
        )
    else:
        code = "unknown_tool"
        raise ToolCallError(code, f"Unknown Lyra MCP tool: {name}")
    return payload


async def _lookup_met_zone(
    arguments: LookupMetZoneInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    match = await backend.lookup_met_zone(arguments.name)
    if match is None:
        _raise_tool_error(
            "unknown_met_zone",
            (
                "No metropolitan zone matched the given name. Check the spelling "
                "or try the official name of a nearby metropolitan zone."
            ),
            {
                "name": arguments.name,
                "action": "Revise name and call lyra_lookup_met_zone again.",
            },
        )
    return _model_dump(match)


async def _search_metrics(
    arguments: SearchMetricsInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    catalog = await backend.get_metrics()
    metrics = list(getattr(catalog, "metrics", []))
    candidates = sorted(
        (_search_candidate(metric, arguments.query) for metric in metrics),
        key=lambda item: (-item["score"], item["metric"]),
    )
    filtered = [candidate for candidate in candidates if candidate["score"] > 0]

    return {
        "query": arguments.query,
        "catalog_fingerprint": getattr(catalog, "catalog_fingerprint", None),
        "candidates": [
            _without_score(candidate) for candidate in filtered[: arguments.limit]
        ],
    }


async def _list_metrics(
    arguments: ListMetricsInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    catalog = await backend.get_metrics()
    fingerprint = str(getattr(catalog, "catalog_fingerprint", ""))
    metrics = sorted(
        getattr(catalog, "metrics", []),
        key=lambda metric: str(getattr(metric, "name", "")),
    )
    offset = _metric_page_offset(arguments.cursor, fingerprint, len(metrics))
    page = metrics[offset : offset + arguments.limit]
    next_offset = offset + len(page)
    next_cursor = (
        _encode_metric_cursor(fingerprint, next_offset)
        if next_offset < len(metrics)
        else None
    )
    return {
        "catalog_fingerprint": fingerprint,
        "total_count": len(metrics),
        "metrics": [
            {
                "name": str(getattr(metric, "name", "")),
                "description": _compact_description(
                    str(getattr(metric, "description", ""))
                ),
            }
            for metric in page
        ],
        "next_cursor": next_cursor,
    }


async def _get_metric(
    arguments: GetMetricInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    metric = await backend.get_metric(arguments.metric)
    if metric is None:
        _raise_tool_error("unknown_metric", f"Unknown metric: {arguments.metric}")
    return _model_dump(metric)


async def _run_metric(
    arguments: RunMetricInput,
    backend: LyraMCPBackend,
) -> RunMetricOutput:
    metric = await backend.get_metric(arguments.metric)
    if metric is None:
        _raise_tool_error("unknown_metric", f"Unknown metric: {arguments.metric}")

    payload = _run_payload_for_metric(
        metric=metric,
        met_zone_code=arguments.met_zone_code,
        parameters=arguments.parameters,
    )
    job = await backend.create_job(
        arguments.metric,
        payload,
    )
    return RunMetricOutput(
        job_id=job.job_id, result_ref=_result_ref_for_job(job.job_id)
    )


async def _observation_for_ref(
    result_ref: str, backend: LyraMCPBackend
) -> tuple[str, JobObservation]:
    job_id = _job_id_from_result_ref(result_ref)
    observation = await backend.observe_job(job_id)
    if observation.result is None:
        snapshot = observation.snapshot
        if snapshot is None:
            _raise_tool_error(
                "result_not_found",
                "This result reference is unknown or expired.",
                {"job_id": job_id, "result_ref": result_ref},
            )
        if snapshot.status == "succeeded":
            _raise_tool_error(
                "result_unavailable",
                "The successful result payload is no longer available.",
                {"job_id": job_id, "result_ref": result_ref},
            )
    return job_id, observation


async def _get_job_result(
    arguments: GetJobResultInput, backend: LyraMCPBackend, public_api_base_url: str
) -> BaseModel:
    job_id, observation = await _observation_for_ref(arguments.result_ref, backend)
    return project_observation(job_id, observation, public_api_base_url)


async def _download_result(
    arguments: ResultRefInput, backend: LyraMCPBackend, *, public_api_base_url: str
) -> DownloadResultOutput:
    job_id, observation = await _observation_for_ref(arguments.result_ref, backend)
    result = observation.result
    status = (
        result.status
        if result
        else observation.snapshot.status
        if observation.snapshot
        else None
    )
    details: JsonObject = {"job_id": job_id, "result_ref": arguments.result_ref}
    if status in {"queued", "running"}:
        _raise_tool_error(
            "result_not_ready",
            "Inspect again after two seconds.",
            {**details, "poll_after_seconds": 2, "next_tool": "lyra_get_job_result"},
        )
    if not isinstance(result, TableJobResult | FileJobResult):
        _raise_tool_error(
            "result_not_downloadable",
            "Failed jobs have no downloadable result.",
            details,
        )
    if isinstance(result, FileJobResult) and not await Path(result.file_path).is_file():
        _raise_tool_error(
            "result_unavailable", "The result artifact is no longer available.", details
        )
    is_table = isinstance(result, TableJobResult)
    return DownloadResultOutput(
        job_id=job_id,
        result_ref=arguments.result_ref,
        format="jsonl" if is_table else "file",
        media_type="application/x-ndjson" if is_table else result.media_type,
        lifetime=observation.lifetime,
        lyra_api=handoff(
            public_api_base_url, job_id, "table.jsonl" if is_table else "download"
        ),
    )


def _run_payload_for_metric(
    *,
    metric: MetricInfo,
    met_zone_code: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    spatial_inputs = getattr(metric, "spatial_inputs", {})
    if not isinstance(spatial_inputs, dict) or not spatial_inputs:
        _raise_tool_error(
            "unsupported_spatial_shape",
            "Metric does not declare a met-zone compatible spatial input.",
        )
    if len(spatial_inputs) != 1:
        _raise_tool_error(
            "unsupported_spatial_shape",
            "MCP v1 supports metrics with exactly one spatial input.",
            {"spatial_inputs": sorted(spatial_inputs)},
        )

    field_name, spatial_kind = next(iter(spatial_inputs.items()))
    if spatial_kind not in {"location", "bounds"}:
        _raise_tool_error(
            "unsupported_spatial_shape",
            f"Unsupported spatial input kind: {spatial_kind}",
        )
    payload: dict[str, Any] = {
        field_name: {"data_type": "met_zone_code", "value": met_zone_code}
    }
    properties = metric.request_schema["properties"]
    if isinstance(properties, dict) and "parameters" in properties:
        payload["parameters"] = parameters
    elif parameters:
        _raise_tool_error(
            "invalid_parameters",
            "This metric does not accept parameters.",
        )
    return payload


def _search_candidate(metric: MetricInfo, query: str) -> dict[str, Any]:
    query_tokens = _tokens(query)
    search_text = str(metric.search_text()) if hasattr(metric, "search_text") else ""
    haystack = _tokens(search_text)
    metric_name = str(getattr(metric, "name", ""))
    name_tokens = _tokens(metric_name)
    description = str(getattr(metric, "description", ""))

    score = 0
    matched_terms: list[str] = []
    for token in query_tokens:
        occurrences = haystack.count(token)
        if occurrences:
            matched_terms.append(token)
            score += occurrences
        if token in name_tokens:
            score += 5
        elif any(name_token.startswith(token) for name_token in name_tokens):
            score += 2

    return {
        "metric": metric_name,
        "description": description,
        "score": score,
        "reason": _search_reason(matched_terms, metric_name, description),
        "required_spatial_fields": _required_spatial_fields(metric),
        "output_kind": getattr(getattr(metric, "output", None), "kind", None),
        "relevant_columns": _relevant_columns(metric, query_tokens),
    }


def _search_reason(
    matched_terms: list[str],
    metric_name: str,
    description: str,
) -> str:
    if matched_terms:
        terms = ", ".join(dict.fromkeys(matched_terms))
        return f"Matches {terms} in the public metric contract."
    if description:
        return f"{metric_name}: {description}"
    return f"{metric_name}: public catalog entry."


def _required_spatial_fields(metric: MetricInfo) -> list[dict[str, str]]:
    spatial_inputs = getattr(metric, "spatial_inputs", {})
    if not isinstance(spatial_inputs, dict):
        return []
    return [
        {"field": str(field), "kind": str(kind)}
        for field, kind in sorted(spatial_inputs.items())
    ]


def _relevant_columns(
    metric: MetricInfo,
    query_tokens: list[str],
) -> list[JsonObject]:
    output = getattr(metric, "output", None)
    columns = list(getattr(output, "columns", []))
    relevant: list[JsonObject] = []
    for column in columns:
        column_payload = _model_dump(column)
        text = " ".join(str(value) for value in column_payload.values())
        if not query_tokens or any(token in _tokens(text) for token in query_tokens):
            relevant.append(column_payload)
    return relevant[:8]


def _without_score(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key != "score"}


def _tokens(value: str) -> list[str]:
    return list(normalize_metric_search_tokens(value))


def _compact_description(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) <= _MAX_COMPACT_DESCRIPTION_LENGTH:
        return compact
    return f"{compact[: _MAX_COMPACT_DESCRIPTION_LENGTH - 1]}…"


def _encode_metric_cursor(fingerprint: str, offset: int) -> str:
    payload = json.dumps(
        {
            "fingerprint": fingerprint,
            "offset": offset,
            "version": _METRIC_CURSOR_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _metric_page_offset(
    cursor: str | None,
    fingerprint: str,
    total_count: int,
) -> int:
    if cursor is None:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            f"{cursor}{padding}",
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
    except (UnicodeDecodeError, ValueError) as exc:
        _raise_invalid_metric_cursor("The cursor is malformed.", exc)

    if not isinstance(payload, dict) or set(payload) != {
        "fingerprint",
        "offset",
        "version",
    }:
        _raise_invalid_metric_cursor("The cursor payload is invalid.")
    if payload["version"] != _METRIC_CURSOR_VERSION:
        _raise_invalid_metric_cursor("The cursor version is unsupported.")
    if payload["fingerprint"] != fingerprint:
        _raise_invalid_metric_cursor("The metric catalog changed between pages.")

    offset = payload["offset"]
    if isinstance(offset, bool) or not isinstance(offset, int):
        _raise_invalid_metric_cursor("The cursor offset is invalid.")
    if offset <= 0 or offset >= total_count:
        _raise_invalid_metric_cursor("The cursor offset is outside the catalog.")
    return offset


def _raise_invalid_metric_cursor(
    reason: str,
    cause: Exception | None = None,
) -> NoReturn:
    error = ToolCallError(
        "invalid_cursor",
        "Metric catalog cursor is invalid or no longer current.",
        {
            "reason": reason,
            "action": "Call lyra_list_metrics again without cursor.",
        },
    )
    if cause is None:
        raise error
    raise error from cause


def _model_dump(value: BaseModel | Mapping[str, JsonValue]) -> JsonObject:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


def _result_ref_for_job(job_id: str) -> str:
    return f"lyra://results/{job_id}"


def _job_id_from_result_ref(result_ref: str) -> str:
    match = _RESULT_REF_PATTERN.fullmatch(result_ref)
    if match is None:
        _raise_tool_error(
            "invalid_result_ref",
            "Invalid Lyra result reference. Expected 'lyra://results/{job_id}'.",
            {"result_ref": result_ref},
        )
    return match.group(1)


def _raise_tool_error(
    code: str,
    message: str,
    details: JsonValue = None,
) -> NoReturn:
    raise ToolCallError(code, message, details)
