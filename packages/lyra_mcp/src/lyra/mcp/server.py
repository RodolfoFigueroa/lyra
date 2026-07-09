from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Annotated, Any, NoReturn, Protocol, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SERVER_INSTRUCTIONS = (
    "Lyra MCP exposes a small set of stable tools for metric catalog search, "
    "metric inspection, met-zone metric runs, and result polling. MCP v1 "
    "accepts only raw metropolitan zone codes for spatial input. Metric runs "
    "return lyra://results/{job_id} references; when a run is still running, "
    "poll the result tools until terminal status before reading preview or raw "
    "metadata. Administrative plugin, worker, queue, and server-management "
    "operations are not available through MCP."
)

_PROTOCOL_VERSION = "2025-06-18"
_JSONRPC_VERSION = "2.0"
_MAX_WAIT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.1
_DEFAULT_POLL_AFTER_SECONDS = 1
_bearer = HTTPBearer(auto_error=False)
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


class LyraMCPBackend(Protocol):
    async def get_metrics(self) -> Any: ...

    async def get_metric(self, metric: str) -> Any | None: ...

    async def create_job(self, metric: str, payload: dict[str, Any]) -> Any: ...

    async def get_job(self, job_id: str) -> Any | None: ...

    async def get_result_descriptor(self, job_id: str) -> Any | None: ...


@dataclass(frozen=True)
class ToolCallError(Exception):
    code: str
    message: str
    details: Any = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {"code": self.code, "message": self.message}
        }
        if self.details is not None:
            payload["error"]["details"] = self.details
        return payload


class InProcessLyraBackend:
    async def get_metrics(self) -> Any:
        from lyra_app.registry import get_metric_catalog  # noqa: PLC0415

        return await asyncio.to_thread(get_metric_catalog)

    async def get_metric(self, metric: str) -> Any | None:
        from lyra_app.registry import get_metric_info  # noqa: PLC0415

        return await asyncio.to_thread(get_metric_info, metric)

    async def create_job(self, metric: str, payload: dict[str, Any]) -> Any:
        from fastapi import HTTPException as FastAPIHTTPException  # noqa: PLC0415
        from lyra.sdk.models import JobCreateRequest  # noqa: PLC0415

        from lyra_app.routes import jobs  # noqa: PLC0415

        try:
            return await jobs.create_job(JobCreateRequest(metric=metric, input=payload))
        except FastAPIHTTPException as exc:
            raise _tool_error_from_http(exc, context="create job") from exc

    async def get_job(self, job_id: str) -> Any | None:
        from fastapi import HTTPException as FastAPIHTTPException  # noqa: PLC0415

        from lyra_app.routes import jobs  # noqa: PLC0415

        try:
            return await jobs.get_job(job_id)
        except FastAPIHTTPException as exc:
            if exc.status_code == 404:
                return None
            raise _tool_error_from_http(exc, context="fetch job status") from exc

    async def get_result_descriptor(self, job_id: str) -> Any | None:
        from fastapi import HTTPException as FastAPIHTTPException  # noqa: PLC0415

        from lyra_app import job_store  # noqa: PLC0415

        try:
            return await job_store.get_job_result_descriptor_async(job_id)
        except FastAPIHTTPException as exc:
            if exc.status_code == 404:
                return None
            raise _tool_error_from_http(exc, context="fetch result descriptor") from exc


def create_mcp_app(
    *,
    api_key: str,
    name: str = "lyra",
    backend: LyraMCPBackend | None = None,
) -> FastAPI:
    tool_backend = backend or InProcessLyraBackend()
    app = FastAPI(
        title="Lyra MCP",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def require_mcp_key(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        if credentials is None:
            raise HTTPException(
                status_code=401,
                detail="MCP bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not hmac.compare_digest(credentials.credentials, api_key):
            raise HTTPException(status_code=403, detail="Invalid MCP bearer token.")

    @app.get("/", dependencies=[Depends(require_mcp_key)])
    async def discovery() -> dict[str, Any]:
        return {
            "name": name,
            "transport": "streamable-http",
            "protocol_version": _PROTOCOL_VERSION,
            "instructions": SERVER_INSTRUCTIONS,
            "tools": _tool_definitions(),
        }

    @app.get("/health", dependencies=[Depends(require_mcp_key)])
    async def health() -> dict[str, str]:
        return {"status": "ok", "name": name}

    @app.post("/", dependencies=[Depends(require_mcp_key)], response_model=None)
    async def handle_message(request: Request) -> JSONResponse | Response:
        try:
            payload = await request.json()
        except JSONDecodeError:
            return _jsonrpc_error(None, -32700, "Parse error")

        if not isinstance(payload, dict):
            return _jsonrpc_error(None, -32600, "Invalid Request")

        return await _handle_rpc_method(
            method=payload.get("method"),
            request_id=payload.get("id"),
            params=payload.get("params"),
            server_name=name,
            backend=tool_backend,
        )

    return app


async def _handle_rpc_method(
    *,
    method: object,
    request_id: Any,
    params: object,
    server_name: str,
    backend: LyraMCPBackend,
) -> JSONResponse | Response:
    if method == "initialize":
        response: JSONResponse | Response = _jsonrpc_result(
            request_id,
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": server_name, "version": "0.1.0"},
                "instructions": SERVER_INSTRUCTIONS,
            },
        )
    elif method == "tools/list":
        response = _jsonrpc_result(request_id, {"tools": _tool_definitions()})
    elif method == "tools/call":
        response = _jsonrpc_result(
            request_id,
            await _handle_tool_call(params=params, backend=backend),
        )
    elif method == "notifications/initialized" or request_id is None:
        response = Response(status_code=202)
    else:
        response = _jsonrpc_error(request_id, -32601, "Method not found")
    return response


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "lyra_search_metrics",
            "description": (
                "Lexically search Lyra's public metric catalog. Use this before "
                "choosing a metric; it returns candidate reasons, required spatial "
                "fields, output kind, and relevant output columns."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Words to match against metric names, descriptions, "
                            "inputs, and outputs."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 5,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "lyra_get_metric",
            "description": (
                "Return the public contract for one Lyra metric, including its "
                "request schema, spatial input metadata, and declared output."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "minLength": 1},
                },
                "required": ["metric"],
                "additionalProperties": False,
            },
        },
        {
            "name": "lyra_run_metric",
            "description": (
                "Start one Lyra metric for a raw metropolitan zone code. Pass "
                "non-spatial inputs in parameters. If the response has "
                "status='running', do not rerun the metric; wait poll_after_seconds "
                "and call lyra_get_job_result, the returned next_tool, with the "
                "returned result_ref or job_id."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "minLength": 1},
                    "met_zone_code": {"type": "string", "minLength": 1},
                    "parameters": {
                        "type": "object",
                        "default": {},
                        "additionalProperties": True,
                    },
                    "wait_seconds": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": _MAX_WAIT_SECONDS,
                        "default": 2,
                    },
                },
                "required": ["metric", "met_zone_code"],
                "additionalProperties": False,
            },
        },
    ]


async def _handle_tool_call(
    *,
    params: object,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    try:
        if not isinstance(params, dict):
            _raise_tool_error(
                "invalid_tool_call",
                "tools/call params must be an object.",
            )
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(tool_name, str):
            _raise_tool_error("invalid_tool_call", "Tool name must be a string.")
        if not isinstance(arguments, dict):
            _raise_tool_error(
                "invalid_tool_call",
                "Tool arguments must be an object.",
            )
        tool_arguments = cast("dict[str, Any]", arguments)

        if tool_name == "lyra_search_metrics":
            payload = await _tool_search_metrics(tool_arguments, backend)
        elif tool_name == "lyra_get_metric":
            payload = await _tool_get_metric(tool_arguments, backend)
        elif tool_name == "lyra_run_metric":
            payload = await _tool_run_metric(tool_arguments, backend)
        else:
            _raise_tool_error(
                "unknown_tool",
                f"Unknown Lyra MCP tool: {tool_name}",
            )
    except ToolCallError as exc:
        return _tool_result(exc.to_payload(), is_error=True)

    return _tool_result(payload)


async def _tool_search_metrics(
    arguments: dict[str, Any],
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    query = _required_string(arguments, "query")
    limit = _bounded_int(
        arguments.get("limit", 5),
        field="limit",
        minimum=1,
        maximum=20,
    )
    catalog = await backend.get_metrics()
    metrics = list(getattr(catalog, "metrics", []))
    candidates = sorted(
        (_search_candidate(metric, query) for metric in metrics),
        key=lambda item: (-item["score"], item["metric"]),
    )
    filtered = [candidate for candidate in candidates if candidate["score"] > 0]
    if not filtered and not _tokens(query):
        filtered = candidates

    return {
        "query": query,
        "catalog_fingerprint": getattr(catalog, "catalog_fingerprint", None),
        "candidates": [_without_score(candidate) for candidate in filtered[:limit]],
    }


async def _tool_get_metric(
    arguments: dict[str, Any],
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    metric_name = _required_string(arguments, "metric")
    metric = await backend.get_metric(metric_name)
    if metric is None:
        _raise_tool_error("unknown_metric", f"Unknown metric: {metric_name}")
    return _model_dump(metric)


async def _tool_run_metric(
    arguments: dict[str, Any],
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    metric_name = _required_string(arguments, "metric")
    met_zone_code = _required_string(arguments, "met_zone_code")
    parameters = arguments.get("parameters", {})
    if not isinstance(parameters, dict):
        _raise_tool_error(
            "invalid_parameters",
            "parameters must be a JSON object.",
            [{"loc": ["parameters"], "msg": "Expected object.", "type": "type"}],
        )
    wait_seconds = _bounded_float(
        arguments.get("wait_seconds", 2),
        field="wait_seconds",
        minimum=0.0,
        maximum=_MAX_WAIT_SECONDS,
    )

    metric = await backend.get_metric(metric_name)
    if metric is None:
        _raise_tool_error("unknown_metric", f"Unknown metric: {metric_name}")

    payload = _run_payload_for_metric(
        metric=metric,
        met_zone_code=met_zone_code,
        parameters=parameters,
    )
    job = await backend.create_job(metric_name, payload)
    job_id = str(job.job_id)
    deadline = time.monotonic() + wait_seconds

    while True:
        status = await _job_status(
            backend, job_id, fallback=getattr(job, "status", None)
        )
        if _is_terminal_status(status):
            descriptor = await backend.get_result_descriptor(job_id)
            if descriptor is None:
                _raise_tool_error(
                    "result_unavailable",
                    f"Job {job_id} finished but its result descriptor is unavailable.",
                )
            return _model_dump(descriptor)
        if time.monotonic() >= deadline:
            return _running_payload(job_id)
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, deadline - time.monotonic()))


def _run_payload_for_metric(
    *,
    metric: Any,
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


def _running_payload(job_id: str) -> dict[str, Any]:
    return {
        "status": "running",
        "job_id": job_id,
        "result_ref": _result_ref_for_job(job_id),
        "poll_after_seconds": _DEFAULT_POLL_AFTER_SECONDS,
        "next_tool": "lyra_get_job_result",
    }


def _search_candidate(metric: Any, query: str) -> dict[str, Any]:
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


def _required_spatial_fields(metric: Any) -> list[dict[str, str]]:
    spatial_inputs = getattr(metric, "spatial_inputs", {})
    if not isinstance(spatial_inputs, dict):
        return []
    return [
        {"field": str(field), "kind": str(kind)}
        for field, kind in sorted(spatial_inputs.items())
    ]


def _relevant_columns(metric: Any, query_tokens: list[str]) -> list[dict[str, Any]]:
    output = getattr(metric, "output", None)
    columns = list(getattr(output, "columns", []))
    batched_columns = list(getattr(output, "batched_columns", []))
    relevant: list[dict[str, Any]] = []
    for column in [*columns, *batched_columns]:
        column_payload = _model_dump(column)
        text = " ".join(str(value) for value in column_payload.values())
        if not query_tokens or any(token in _tokens(text) for token in query_tokens):
            relevant.append(column_payload)
    return relevant[:8]


def _without_score(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key != "score"}


def _tokens(value: str) -> list[str]:
    return _TOKEN_PATTERN.findall(value.lower())


def _required_string(arguments: dict[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        _raise_tool_error(
            "invalid_parameters",
            f"{field} must be a non-empty string.",
            [{"loc": [field], "msg": "Expected non-empty string.", "type": "type"}],
        )
    return value.strip()


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _raise_tool_error(
            "invalid_parameters",
            f"{field} must be an integer.",
            [{"loc": [field], "msg": "Expected integer.", "type": "type"}],
        )
    if value < minimum or value > maximum:
        _raise_tool_error(
            "invalid_parameters",
            f"{field} must be between {minimum} and {maximum}.",
            [{"loc": [field], "msg": "Out of range.", "type": "range"}],
        )
    return value


def _bounded_float(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        _raise_tool_error(
            "invalid_parameters",
            f"{field} must be a number.",
            [{"loc": [field], "msg": "Expected number.", "type": "type"}],
        )
    resolved = float(value)
    if resolved < minimum:
        return minimum
    return min(resolved, maximum)


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return value
    return dict(value)


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            }
        ],
        "structuredContent": payload,
    }
    if is_error:
        result["isError"] = True
    return result


def _result_ref_for_job(job_id: str) -> str:
    return f"lyra://results/{job_id}"


def _tool_error_from_http(exc: HTTPException, *, context: str) -> ToolCallError:
    if exc.status_code == 404:
        code = "unknown_metric"
    elif exc.status_code == 422:
        code = "invalid_parameters"
    else:
        code = "backend_error"
    return ToolCallError(code, f"Failed to {context}.", exc.detail)


def _raise_tool_error(code: str, message: str, details: Any = None) -> NoReturn:
    raise ToolCallError(code, message, details)


def _jsonrpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": _JSONRPC_VERSION,
            "id": request_id,
            "result": result,
        }
    )


def _jsonrpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": _JSONRPC_VERSION,
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        },
        status_code=400,
    )
