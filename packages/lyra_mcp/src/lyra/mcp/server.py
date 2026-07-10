from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from lyra.mcp.models import (
    MAX_METRIC_PAGE_SIZE,
    TOOL_CONTRACTS,
    TOOL_CONTRACTS_BY_NAME,
)
from lyra.mcp.tools import (
    InProcessLyraBackend,
    LyraMCPBackend,
    ToolCallError,
    execute_tool,
)
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.routing import Route

from lyra_app.agent_auth import AgentBearerAuthMiddleware
from lyra_app.config import ApiConfig
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.types import Receive, Scope, Send

SERVER_INSTRUCTIONS = (
    "Lyra MCP exposes stable tools for metric discovery, inspection, met-zone "
    "metric runs, and result polling. Use lyra_search_metrics with meaningful "
    "task terms when selecting a metric for a specific question. Use "
    "lyra_list_metrics only for explicit catalog-inventory requests or after "
    "focused searches return no candidates. Inspect a selected metric with "
    "lyra_get_metric. MCP v1 "
    "accepts only raw metropolitan zone codes for spatial input. Metric runs "
    "return lyra://results/{job_id} references; when a run is still running, "
    "poll the result tools until terminal status before reading preview or raw "
    "metadata. Administrative plugin, worker, queue, and server-management "
    "operations are not available through MCP."
)

_DEFAULT_ALLOWED_HOSTS = [
    "127.0.0.1:*",
    "localhost:*",
    "testserver",
    "testserver:*",
]


def create_mcp_app(
    *,
    agent_api_key: str,
    public_api_base_url: str,
    name: str = "lyra",
    backend: LyraMCPBackend | None = None,
) -> Starlette:
    public_api_base_url = ApiConfig(public_base_url=public_api_base_url).public_base_url
    tool_backend = backend or InProcessLyraBackend()
    server = Server(
        name=name,
        version="0.1.0",
        instructions=SERVER_INSTRUCTIONS,
    )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=contract.name,
                description=contract.description,
                inputSchema=contract.input_schema,
                outputSchema=contract.output_schema,
                annotations=ToolAnnotations(
                    readOnlyHint=contract.read_only,
                    destructiveHint=False,
                    idempotentHint=contract.idempotent,
                    openWorldHint=contract.open_world,
                ),
            )
            for contract in TOOL_CONTRACTS
        ]

    # Validate with the contract models below so failures can include structured,
    # actionable corrections instead of the SDK's text-only JSON Schema error.
    @server.call_tool(validate_input=False)
    async def call_tool(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[list[TextContent], dict[str, Any]] | CallToolResult:
        contract = TOOL_CONTRACTS_BY_NAME.get(tool_name)
        if contract is None:
            return _domain_error_result(
                ToolCallError(
                    "unknown_tool",
                    f"Unknown Lyra MCP tool: {tool_name}",
                )
            )

        try:
            validated_arguments = contract.input_model.model_validate(arguments)
        except ValidationError as exc:
            return _invalid_argument_result(tool_name, arguments, exc)

        try:
            payload = await execute_tool(
                tool_name,
                validated_arguments,
                tool_backend,
                public_api_base_url=public_api_base_url,
            )
        except ToolCallError as exc:
            return _domain_error_result(exc)

        return [_text_content(payload)], payload

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_DEFAULT_ALLOWED_HOSTS,
            allowed_origins=[],
        ),
    )
    transport = AgentBearerAuthMiddleware(
        _StreamableHTTPApplication(session_manager),
        agent_api_key=agent_api_key,
    )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    app = Starlette(routes=[Route("/", endpoint=transport)], lifespan=lifespan)
    app.state.session_manager = session_manager
    return app


class _StreamableHTTPApplication:
    def __init__(self, session_manager: StreamableHTTPSessionManager) -> None:
        self._session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._session_manager.handle_request(scope, receive, send)


def _text_content(payload: dict[str, Any]) -> TextContent:
    return TextContent(
        type="text",
        text=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _domain_error_result(error: ToolCallError) -> CallToolResult:
    payload = error.to_payload()
    return CallToolResult(
        content=[_text_content(payload)],
        structuredContent=payload,
        isError=True,
    )


def _invalid_argument_result(
    tool_name: str,
    arguments: dict[str, Any],
    error: ValidationError,
) -> CallToolResult:
    issues = [
        {
            "location": list(issue["loc"]),
            "message": issue["msg"],
            "type": issue["type"],
        }
        for issue in error.errors(include_url=False)
    ]
    details: dict[str, Any] = {"issues": issues}
    limit = arguments.get("limit")
    if tool_name in {"lyra_list_metrics", "lyra_search_metrics"} and any(
        issue["location"] == ["limit"] for issue in issues
    ):
        details["allowed_bounds"] = {
            "limit": {"minimum": 1, "maximum": MAX_METRIC_PAGE_SIZE}
        }
        if isinstance(limit, int) and not isinstance(limit, bool):
            details["suggested_arguments"] = {
                **arguments,
                "limit": min(max(limit, 1), MAX_METRIC_PAGE_SIZE),
            }

    payload = ToolCallError(
        "invalid_arguments",
        f"Invalid arguments for {tool_name}.",
        details,
    ).to_payload()
    return CallToolResult(
        content=[_text_content(payload)],
        structuredContent=payload,
        isError=True,
    )


__all__ = [
    "SERVER_INSTRUCTIONS",
    "InProcessLyraBackend",
    "LyraMCPBackend",
    "ToolCallError",
    "create_mcp_app",
]
