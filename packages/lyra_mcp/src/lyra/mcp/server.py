from __future__ import annotations

import hmac
from json import JSONDecodeError
from typing import Annotated, Any

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
_bearer = HTTPBearer(auto_error=False)


def create_mcp_app(*, api_key: str, name: str = "lyra") -> FastAPI:
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
            "tools": [],
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

        return _handle_rpc_method(
            method=payload.get("method"),
            request_id=payload.get("id"),
            server_name=name,
        )

    return app


def _handle_rpc_method(
    *,
    method: object,
    request_id: Any,
    server_name: str,
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
        response = _jsonrpc_result(request_id, {"tools": []})
    elif method == "notifications/initialized" or request_id is None:
        response = Response(status_code=202)
    else:
        response = _jsonrpc_error(request_id, -32601, "Method not found")
    return response


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
