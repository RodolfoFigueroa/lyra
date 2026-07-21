from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast
from urllib.parse import quote, urlsplit, urlunsplit

from lyra.sdk.models.metric import normalize_metric_search_tokens
from lyra.sdk.types import validate_json_value
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import HTTPException
    from lyra.sdk.models import JobCreateResponse, JobStatusInfo, ResultDescriptor
    from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfoV3
    from lyra.sdk.types import JsonObject, JsonValue

    from lyra_app.db.connection import ApplicationDatabaseRuntime
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

_POLL_INTERVAL_SECONDS = 0.1
_DEFAULT_POLL_AFTER_SECONDS = 1
_RESULT_REF_PATTERN = re.compile(r"^lyra://results/([^/?#\s]+)$")
_UNKNOWN_METRIC_ERROR = "unknown_metric"
_INVALID_PARAMETERS_ERROR = "invalid_parameters"
_IDEMPOTENCY_CONFLICT_ERROR = "idempotency_conflict"
_RATE_LIMITED_ERROR = "rate_limited"
_BACKEND_ERROR = "backend_error"
_DATABASE_UNAVAILABLE_ERROR = "database_unavailable"
_METRIC_CURSOR_VERSION = 1
_MAX_COMPACT_DESCRIPTION_LENGTH = 240


class LyraMCPBackend(Protocol):
    async def get_metrics(self) -> MetricCatalogResponse: ...

    async def lookup_met_zone(self, name: str) -> dict[str, str] | None: ...

    async def get_metric(self, metric: str) -> MetricInfoV3 | None: ...

    async def create_job(
        self,
        metric: str,
        payload: JsonObject,
        *,
        idempotency_key: str | None = None,
    ) -> JobCreateResponse: ...

    async def get_job(self, job_id: str) -> JobStatusInfo | None: ...

    async def get_result_descriptor(self, job_id: str) -> ResultDescriptor | None: ...


@dataclass(frozen=True)
class ToolCallError(Exception):
    """Transport-independent domain failure returned as a structured tool error."""

    code: str
    message: str
    details: JsonValue = None

    def to_payload(self) -> dict[str, Any]:
        error: JsonObject = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


class InProcessLyraBackend:
    def __init__(self, database: ApplicationDatabaseRuntime | None = None) -> None:
        self.database = database

    async def get_metrics(self) -> MetricCatalogResponse:
        from lyra_app.registry import get_metric_catalog  # noqa: PLC0415

        return await asyncio.to_thread(get_metric_catalog)

    async def lookup_met_zone(self, name: str) -> dict[str, str] | None:
        from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415

        from lyra_app.db.connection import (  # noqa: PLC0415
            is_database_unavailable_error,
        )
        from lyra_app.loaders.db import (  # noqa: PLC0415
            get_met_zone_code_from_name_async,
        )

        if self.database is None:
            msg = "Application database runtime is unavailable."
            raise RuntimeError(msg)
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

    async def get_metric(self, metric: str) -> MetricInfoV3 | None:
        from lyra_app.registry import get_metric_info  # noqa: PLC0415

        return await asyncio.to_thread(get_metric_info, metric)

    async def create_job(
        self,
        metric: str,
        payload: JsonObject,
        *,
        idempotency_key: str | None = None,
    ) -> JobCreateResponse:
        from lyra.sdk.models import JobCreateRequest  # noqa: PLC0415

        from lyra_app.db.connection import DatabaseUnavailableError  # noqa: PLC0415
        from lyra_app.job_submission import (  # noqa: PLC0415
            IdempotencyConflictError,
            SubmissionRateLimitedError,
            SubmissionUnavailableError,
            UnknownMetricError,
            submit_job,
        )
        from lyra_app.registry import MetricPayloadValidationError  # noqa: PLC0415
        from lyra_app.spatial_inputs import (  # noqa: PLC0415
            SpatialInputResolutionUnavailableError,
            SpatialInputValidationError,
        )

        try:
            return await submit_job(
                JobCreateRequest(
                    metric=metric,
                    input=payload,
                    idempotency_key=idempotency_key,
                ),
                database=self.database,
            )
        except UnknownMetricError as exc:
            raise ToolCallError(_UNKNOWN_METRIC_ERROR, str(exc)) from exc
        except (MetricPayloadValidationError, SpatialInputValidationError) as exc:
            raise ToolCallError(
                _INVALID_PARAMETERS_ERROR,
                "Invalid metric parameters.",
                validate_json_value(exc.errors),
            ) from exc
        except IdempotencyConflictError as exc:
            raise ToolCallError(
                _IDEMPOTENCY_CONFLICT_ERROR,
                str(exc),
                validate_json_value(exc.details),
            ) from exc
        except SubmissionRateLimitedError as exc:
            raise ToolCallError(
                _RATE_LIMITED_ERROR,
                str(exc),
                validate_json_value(exc.details),
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
                retry_after = (
                    self.database.config.database.retry_after_seconds
                    if self.database is not None
                    else 5
                )
                raise ToolCallError(
                    _DATABASE_UNAVAILABLE_ERROR,
                    "The spatial database is temporarily unavailable.",
                    {"retryable": True, "retry_after_seconds": retry_after},
                ) from exc
            raise ToolCallError(
                _BACKEND_ERROR, "Failed to create job.", str(exc)
            ) from exc

    async def get_job(self, job_id: str) -> JobStatusInfo | None:
        from fastapi import HTTPException as FastAPIHTTPException  # noqa: PLC0415

        from lyra_app.routes import jobs  # noqa: PLC0415

        try:
            return await jobs.get_job(job_id)
        except FastAPIHTTPException as exc:
            if exc.status_code == 404:
                return None
            raise _tool_error_from_http(exc, context="fetch job status") from exc

    async def get_result_descriptor(self, job_id: str) -> ResultDescriptor | None:
        from fastapi import HTTPException as FastAPIHTTPException  # noqa: PLC0415

        from lyra_app import job_store  # noqa: PLC0415

        try:
            return await job_store.get_job_result_descriptor_async(job_id)
        except FastAPIHTTPException as exc:
            if exc.status_code == 404:
                return None
            raise _tool_error_from_http(exc, context="fetch result descriptor") from exc


async def execute_tool(
    name: str,
    arguments: MCPContractModel,
    backend: LyraMCPBackend,
    *,
    public_api_base_url: str,
) -> dict[str, Any]:
    """Execute one validated tool call against the Lyra domain service."""

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
        payload = await _get_job_result(cast("GetJobResultInput", arguments), backend)
    elif name == "lyra_get_result_metadata":
        payload = await _get_result_metadata(cast("ResultRefInput", arguments), backend)
    elif name == "lyra_get_result_preview":
        payload = await _get_result_preview(cast("ResultRefInput", arguments), backend)
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
) -> dict[str, Any]:
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
        idempotency_key=arguments.idempotency_key,
    )
    job_id = str(job.job_id)
    reused = bool(getattr(job, "reused", False))
    deadline = time.monotonic() + arguments.wait_seconds

    while True:
        status = await _job_status(
            backend,
            job_id,
            fallback=getattr(job, "status", None),
        )
        if _is_terminal_status(status):
            descriptor = await backend.get_result_descriptor(job_id)
            if descriptor is None:
                _raise_tool_error(
                    "result_unavailable",
                    f"Job {job_id} finished but its result descriptor is unavailable.",
                )
            return {**_model_dump(descriptor), "reused": reused}
        if time.monotonic() >= deadline:
            return _running_payload(job_id, reused=reused)
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, deadline - time.monotonic()))


async def _get_job_result(
    arguments: GetJobResultInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    job_id = _job_id_from_result_ref(arguments.result_ref)
    deadline = time.monotonic() + arguments.wait_seconds

    while True:
        snapshot = await backend.get_job(job_id)
        if snapshot is None:
            descriptor = await backend.get_result_descriptor(job_id)
            if descriptor is not None:
                return _model_dump(descriptor)
            _raise_result_expired(job_id)

        status = str(getattr(snapshot, "status", ""))
        if _is_terminal_status(status):
            descriptor = await backend.get_result_descriptor(job_id)
            if descriptor is None:
                _raise_result_expired(job_id)
            return _model_dump(descriptor)

        if time.monotonic() >= deadline:
            return _running_payload(job_id)
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, deadline - time.monotonic()))


async def _get_result_metadata(
    arguments: ResultRefInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    descriptor = await _descriptor_for_result_ref(arguments.result_ref, backend)
    payload = _model_dump(descriptor)
    return {
        "schema_version": payload["schema_version"],
        "job_id": payload["job_id"],
        "status": payload["status"],
        "result_kind": payload["result_kind"],
        "result_ref": payload["result_ref"],
        "provenance": payload.get("provenance"),
        "completed_at": payload["completed_at"],
        "lifetime": payload.get("lifetime", {}),
        "table": payload.get("table"),
        "file": payload.get("file"),
        "summary": payload["summary"],
        "error": payload.get("error"),
    }


async def _get_result_preview(
    arguments: ResultRefInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    descriptor = await _descriptor_for_result_ref(arguments.result_ref, backend)
    payload = _model_dump(descriptor)
    return {
        "schema_version": payload["schema_version"],
        "job_id": payload["job_id"],
        "status": payload["status"],
        "result_kind": payload["result_kind"],
        "result_ref": payload["result_ref"],
        "provenance": payload.get("provenance"),
        "completed_at": payload["completed_at"],
        "lifetime": payload.get("lifetime", {}),
        "preview": payload.get("preview", {}),
        "summary": payload["summary"],
        "error": payload.get("error"),
    }


async def _download_result(
    arguments: ResultRefInput,
    backend: LyraMCPBackend,
    *,
    public_api_base_url: str,
) -> dict[str, Any]:
    referenced_job_id = _job_id_from_result_ref(arguments.result_ref)
    descriptor = await _descriptor_for_result_ref(arguments.result_ref, backend)
    payload = _model_dump(descriptor)
    raw_value = payload["raw"]
    if not isinstance(raw_value, dict):
        _raise_tool_error(
            "invalid_result_descriptor",
            "Result descriptor raw metadata must be an object.",
        )
    raw = raw_value
    jsonl_path = raw.get("jsonl_path")
    formats = raw.get("formats", [])
    supports_jsonl = isinstance(formats, list) and "jsonl" in formats
    if (
        payload["result_kind"] != "table"
        or not supports_jsonl
        or not isinstance(jsonl_path, str)
        or not jsonl_path
    ):
        _raise_tool_error(
            "unsupported_result_download",
            "Only table results can be downloaded as JSONL through MCP v1.",
            validate_json_value(
                {
                    "job_id": payload["job_id"],
                    "result_ref": payload["result_ref"],
                    "result_kind": payload["result_kind"],
                    "formats": formats,
                }
            ),
        )

    lifetime = payload.get("lifetime")
    if not isinstance(lifetime, dict):
        lifetime = {}
    return {
        "job_id": payload["job_id"],
        "result_ref": payload["result_ref"],
        "status": payload["status"],
        "format": "jsonl",
        "media_type": "application/x-ndjson",
        "lyra_api": {
            "method": "GET",
            "url": _jsonl_download_url(
                public_api_base_url,
                referenced_job_id,
            ),
            "authentication": {
                "scheme": "Bearer",
                "credential_env_var": "LYRA_AGENT_API_KEY",
            },
        },
        "client_helpers": {
            "python_sync": (
                "LyraAPIClient.download_result(result_ref, path, format='jsonl')"
            ),
            "python_async": (
                "AsyncLyraAPIClient.download_result(result_ref, path, format='jsonl')"
            ),
        },
        "expires_in_seconds": lifetime.get("expires_in_seconds"),
        "expires_at": lifetime.get("expires_at"),
    }


def _jsonl_download_url(public_api_base_url: str, job_id: str) -> str:
    base = urlsplit(public_api_base_url)
    job_segment = quote(job_id, safe="-._~")
    path = f"{base.path.rstrip('/')}/jobs/{job_segment}/result/table.jsonl"
    return urlunsplit((base.scheme, base.netloc, path, "", ""))


async def _descriptor_for_result_ref(
    result_ref: str,
    backend: LyraMCPBackend,
) -> ResultDescriptor:
    job_id = _job_id_from_result_ref(result_ref)
    descriptor = await backend.get_result_descriptor(job_id)
    if descriptor is None:
        _raise_result_expired(job_id)
    return descriptor


def _run_payload_for_metric(
    *,
    metric: MetricInfoV3,
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
    if field_name in parameters:
        _raise_tool_error(
            "invalid_parameters",
            (
                f"parameters must not include spatial field {field_name!r}; "
                "use met_zone_code."
            ),
            [
                {
                    "loc": ["parameters", field_name],
                    "msg": "Spatial input is owned by MCP.",
                    "type": "value_error",
                }
            ],
        )

    payload = dict(parameters)
    payload[field_name] = {"data_type": "met_zone_code", "value": met_zone_code}
    return payload


async def _job_status(
    backend: LyraMCPBackend,
    job_id: str,
    *,
    fallback: object,
) -> str:
    snapshot = await backend.get_job(job_id)
    status = getattr(snapshot, "status", fallback)
    return str(status)


def _is_terminal_status(status: str) -> bool:
    return status in {"succeeded", "failed", "cancelled"}


def _running_payload(
    job_id: str,
    *,
    reused: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "running",
        "job_id": job_id,
        "result_ref": _result_ref_for_job(job_id),
        "poll_after_seconds": _DEFAULT_POLL_AFTER_SECONDS,
        "next_tool": "lyra_get_job_result",
    }
    if reused is not None:
        payload["reused"] = reused
    return payload


def _search_candidate(metric: MetricInfoV3, query: str) -> dict[str, Any]:
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


def _required_spatial_fields(metric: MetricInfoV3) -> list[dict[str, str]]:
    spatial_inputs = getattr(metric, "spatial_inputs", {})
    if not isinstance(spatial_inputs, dict):
        return []
    return [
        {"field": str(field), "kind": str(kind)}
        for field, kind in sorted(spatial_inputs.items())
    ]


def _relevant_columns(
    metric: MetricInfoV3,
    query_tokens: list[str],
) -> list[JsonObject]:
    output = getattr(metric, "output", None)
    columns = list(getattr(output, "columns", []))
    batched_columns = list(getattr(output, "batched_columns", []))
    relevant: list[JsonObject] = []
    for column in [*columns, *batched_columns]:
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
        return value.model_dump(mode="json", exclude_none=True)
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


def _raise_result_expired(job_id: str) -> NoReturn:
    _raise_tool_error(
        "result_expired",
        (
            f"Result for job {job_id} expired or was not found. Rerun the metric "
            "if the user still wants this data."
        ),
        {
            "job_id": job_id,
            "result_ref": _result_ref_for_job(job_id),
            "rerun_required": True,
        },
    )


def _tool_error_from_http(exc: HTTPException, *, context: str) -> ToolCallError:
    if exc.status_code == 404:
        code = "unknown_metric"
    elif exc.status_code == 422:
        code = "invalid_parameters"
    elif exc.status_code == 409:
        code = "idempotency_conflict"
    elif exc.status_code == 429:
        code = "rate_limited"
    else:
        code = "backend_error"
    return ToolCallError(code, f"Failed to {context}.", exc.detail)


def _raise_tool_error(
    code: str,
    message: str,
    details: JsonValue = None,
) -> NoReturn:
    raise ToolCallError(code, message, details)


__all__ = [
    "InProcessLyraBackend",
    "LyraMCPBackend",
    "ToolCallError",
    "execute_tool",
]
