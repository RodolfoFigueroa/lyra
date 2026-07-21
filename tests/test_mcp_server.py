from __future__ import annotations

import asyncio
import base64
import json
import threading
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, Unpack

import httpx
import pytest
from lyra.sdk.models import (
    CancelledJobResult,
    FailedJobResult,
    FileJobResult,
    JobCreateResponse,
    JobLifecycleStatus,
    JobLinks,
    JobRunProvenance,
    JobStatusInfo,
    ResultDescriptor,
    ResultLifetime,
    TableJobResult,
    build_result_descriptor,
)
from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfoV4
from lyra.sdk.models.plugin_v4 import (
    FileOutputV4,
    SpatialInputKindV4,
    TableOutputColumnV4,
    TableOutputV4,
)
from lyra.sdk.types import (
    JsonObject,
    JsonValue,
    validate_json_object,
    validate_json_value,
)
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra_app import main
from lyra_app.mcp import SERVER_INSTRUCTIONS
from lyra_app.mcp import create_mcp_app as _create_mcp_app
from lyra_app.mcp.models import TOOL_CONTRACTS_BY_NAME
from tests.config_helpers import load_test_config

_COMPLETED_AT = datetime(2026, 7, 9, 12, 5, tzinfo=UTC)
create_mcp_app = partial(
    _create_mcp_app,
    public_api_base_url="https://lyra.example.test/api",
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from lyra_app.config import LyraConfig


class _RequestOptions(TypedDict):
    headers: NotRequired[dict[str, str]]
    json: NotRequired[JsonObject]
    content: NotRequired[str]


def _initialize_payload() -> JsonObject:
    return validate_json_object(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        }
    )


def _mcp_headers(bearer: str | None = None) -> dict[str, str]:
    token = "agent-secret" if bearer is None else bearer
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }


def _tool_call_payload(
    name: str,
    arguments: dict[str, Any],
    *,
    request_id: int = 10,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _tool_payload(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200
    result = response.json()["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"]


def _metric_cursor_payload(
    *,
    fingerprint: str = "catalog-1",
    offset: int = 1,
    version: int = 1,
) -> str:
    payload = json.dumps(
        {"fingerprint": fingerprint, "offset": offset, "version": version},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


class _ManagedTestClient:
    def __init__(self, app: Starlette) -> None:
        self._app = app
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._client: httpx.AsyncClient | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5)
        _MANAGED_CLIENTS.append(self)

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with (
            self._app.router.lifespan_context(self._app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self._app),
                base_url="http://testserver",
            ) as client,
        ):
            self._client = client
            self._ready.set()
            await self._stop.wait()

    def get(self, path: str, **kwargs: Unpack[_RequestOptions]) -> httpx.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Unpack[_RequestOptions]) -> httpx.Response:
        return self._request("POST", path, **kwargs)

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Unpack[_RequestOptions],
    ) -> httpx.Response:
        assert self._client is not None
        assert self._loop is not None
        request = self._client.request(method, path, **kwargs)
        return asyncio.run_coroutine_threadsafe(request, self._loop).result(timeout=10)

    def close(self) -> None:
        if self._client is None:
            return
        assert self._loop is not None
        assert self._stop is not None
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()
        self._client = None


_MANAGED_CLIENTS: list[_ManagedTestClient] = []


@pytest.fixture(autouse=True)
def _close_managed_clients() -> Iterator[None]:
    yield
    while _MANAGED_CLIENTS:
        _MANAGED_CLIENTS.pop().close()


def _app_with_mcp(
    config: LyraConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> _ManagedTestClient:
    monkeypatch.setattr(
        main, "bootstrap_runtime", lambda runtime_config: runtime_config
    )

    from lyra_app import registry  # noqa: PLC0415

    monkeypatch.setattr(registry, "ensure_catalog_loaded", lambda: None)

    async def noop() -> None:
        return None

    monkeypatch.setattr(main, "start_worker_inspect_collector", noop)
    monkeypatch.setattr(main, "stop_worker_inspect_collector", noop)
    return _ManagedTestClient(main.create_app(config))


class FakeMCPBackend:
    def __init__(self, metrics: list[MetricInfoV4]) -> None:
        self.catalog = MetricCatalogResponse(
            client_schema_version=1,
            json_schema_dialect="https://json-schema.org/draft/2020-12/schema",
            catalog_fingerprint="catalog-1",
            metrics=metrics,
        )
        self.jobs: dict[str, JobStatusInfo] = {}
        self.descriptors: dict[str, ResultDescriptor] = {}
        self.payloads: list[dict[str, Any]] = []
        self.idempotency_records: dict[
            str,
            tuple[str, dict[str, Any], str],
        ] = {}
        self.job_status_sequence: list[JobLifecycleStatus] = ["succeeded"]
        self.met_zone_matches: dict[str, dict[str, str]] = {
            "Mexico City": {
                "cve_met": "09.01",
                "nom_met": "Valle de México",
            },
            "Mexcio City": {
                "cve_met": "09.01",
                "nom_met": "Valle de México",
            },
        }
        self.met_zone_queries: list[str] = []

    async def get_metrics(self) -> MetricCatalogResponse:
        return self.catalog

    async def lookup_met_zone(self, name: str) -> dict[str, str] | None:
        self.met_zone_queries.append(name)
        return self.met_zone_matches.get(name)

    async def get_metric(self, metric: str) -> MetricInfoV4 | None:
        return next(
            (
                candidate
                for candidate in self.catalog.metrics
                if candidate.name == metric
            ),
            None,
        )

    async def create_job(
        self,
        metric: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> JobCreateResponse:
        if payload.get("value") == "invalid":
            code = "invalid_parameters"
            message = "Invalid metric parameters."
            details = [{"loc": ["value"], "msg": "Expected integer.", "type": "type"}]
            raise self._tool_error(
                code,
                message,
                validate_json_value(details),
            )

        if idempotency_key is not None and idempotency_key in self.idempotency_records:
            prior_metric, prior_payload, prior_job_id = self.idempotency_records[
                idempotency_key
            ]
            if (prior_metric, prior_payload) != (metric, payload):
                code = "idempotency_conflict"
                message = "The idempotency key is already bound to a different request."
                raise self._tool_error(
                    code,
                    message,
                    {"idempotency_key": idempotency_key, "job_id": prior_job_id},
                )
            return self._job_response(prior_job_id, metric, reused=True)

        job_id = f"job-{len(self.payloads) + 1}"
        self.payloads.append(payload)
        if idempotency_key is not None:
            self.idempotency_records[idempotency_key] = (metric, payload, job_id)
        self.jobs[job_id] = self._job_status(
            job_id, self.job_status_sequence[0], metric
        )
        self.descriptors[job_id] = build_result_descriptor(
            TableJobResult(
                job_id=job_id,
                index=["area-1"],
                columns=["value"],
                data=[[payload.get("value", 1)]],
            ),
            completed_at=_COMPLETED_AT,
        )
        return self._job_response(job_id, metric, reused=False)

    @staticmethod
    def _job_response(
        job_id: str,
        metric: str,
        *,
        reused: bool,
    ) -> JobCreateResponse:
        return JobCreateResponse(
            job_id=job_id,
            metric=metric,
            status="queued",
            reused=reused,
            links=JobLinks(
                self=f"/jobs/{job_id}",
                events=f"/jobs/{job_id}/events",
                result=f"/jobs/{job_id}/result",
            ),
        )

    async def get_job(self, job_id: str) -> JobStatusInfo | None:
        if job_id not in self.jobs:
            return None
        position = min(
            len(self.payloads_for_job(job_id)), len(self.job_status_sequence) - 1
        )
        status = self.job_status_sequence[position]
        self.payloads.append({"_poll": job_id})
        self.jobs[job_id] = self._job_status(job_id, status, self.jobs[job_id].metric)
        return self.jobs[job_id]

    async def get_result_descriptor(self, job_id: str) -> ResultDescriptor | None:
        return self.descriptors.get(job_id)

    def payloads_for_job(self, job_id: str) -> list[dict[str, Any]]:
        return [payload for payload in self.payloads if payload.get("_poll") == job_id]

    @staticmethod
    def _job_status(
        job_id: str,
        status: JobLifecycleStatus,
        metric: str | None,
    ) -> JobStatusInfo:
        return JobStatusInfo(
            job_id=job_id,
            status=status,
            updated_at=datetime.now(UTC),
            metric=metric,
        )

    @staticmethod
    def _tool_error(code: str, message: str, details: JsonValue) -> Exception:
        from lyra_app.mcp.server import ToolCallError  # noqa: PLC0415

        return ToolCallError(code, message, details)


def _table_metric(
    name: str,
    description: str,
    *,
    spatial_inputs: dict[str, SpatialInputKindV4] | None = None,
    value_type: str = "integer",
) -> MetricInfoV4:
    spatial = spatial_inputs or {"location": "location"}
    properties: dict[str, Any] = {
        field: {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "data_type": {"const": "met_zone_code"},
                        "value": {"type": "string"},
                    },
                    "required": ["data_type", "value"],
                }
            ]
        }
        for field in spatial
    }
    properties["value"] = {
        "type": value_type,
        "description": "Value copied into each output row.",
    }
    return MetricInfoV4(
        name=name,
        description=description,
        request_schema={
            "type": "object",
            "properties": properties,
            "required": [*spatial, "value"],
            "additionalProperties": False,
        },
        spatial_inputs=spatial,
        output=TableOutputV4(
            kind="table",
            columns=[
                TableOutputColumnV4(
                    name="value",
                    type="integer",
                    unit="count",
                    description="Submitted value.",
                )
            ],
        ),
    )


def _file_metric(name: str, description: str) -> MetricInfoV4:
    return MetricInfoV4(
        name=name,
        description=description,
        request_schema={
            "type": "object",
            "properties": {"location": {"type": "object"}},
            "required": ["location"],
            "additionalProperties": False,
        },
        spatial_inputs={"location": "location"},
        output=FileOutputV4(kind="file", media_type="text/plain", extensions=[".txt"]),
    )


def test_mcp_package_initializes_with_bearer_auth() -> None:
    app = create_mcp_app(agent_api_key="agent-secret")
    client = _ManagedTestClient(app)

    missing = client.post("/", json=_initialize_payload())
    invalid = client.post(
        "/",
        json=_initialize_payload(),
        headers=_mcp_headers("wrong"),
    )
    initialized = client.post(
        "/",
        json=_initialize_payload(),
        headers=_mcp_headers(),
    )

    assert missing.status_code == 401
    assert invalid.status_code == 403
    assert initialized.status_code == 200
    result = initialized.json()["result"]
    assert result["serverInfo"]["name"] == "lyra"
    assert result["capabilities"] == {
        "experimental": {},
        "tools": {"listChanged": False},
    }
    assert result["instructions"] == SERVER_INSTRUCTIONS
    assert "metropolitan zone codes" in result["instructions"]
    assert "lyra://results/{job_id}" in result["instructions"]
    assert "poll the result tools" in result["instructions"]


def test_official_client_initializes_lists_calls_and_closes_cleanly() -> None:
    metric = _table_metric("smoke_table_metric", "Return a table.")
    mcp_app = create_mcp_app(
        agent_api_key="agent-secret",
        backend=FakeMCPBackend([metric]),
    )
    mounted_app = Starlette(routes=[Mount("/mcp", app=mcp_app)])

    async def use_official_client() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mounted_app),
                base_url="http://testserver",
                headers=_mcp_headers(),
                follow_redirects=True,
            ) as http_client,
            streamable_http_client(
                "http://testserver/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await session.initialize()
            tools = await session.list_tools()
            called = await session.call_tool(
                "lyra_get_metric",
                {"metric": "smoke_table_metric"},
            )
            lookup = await session.call_tool(
                "lyra_lookup_met_zone",
                {"name": "Mexico City"},
            )
            extra_argument = await session.call_tool(
                "lyra_get_metric",
                {"metric": "smoke_table_metric", "unexpected": True},
            )
            invalid_type = await session.call_tool(
                "lyra_search_metrics",
                {"query": "smoke", "limit": "2"},
            )
            unknown = await session.call_tool("lyra_not_a_tool", {})
            return (
                initialized,
                tools,
                called,
                lookup,
                extra_argument,
                invalid_type,
                unknown,
            )

    (
        initialized,
        tools,
        called,
        lookup,
        extra_argument,
        invalid_type,
        unknown,
    ) = asyncio.run(use_official_client())

    assert initialized.serverInfo.name == "lyra"
    assert initialized.instructions == SERVER_INSTRUCTIONS
    tool_names = {tool.name for tool in tools.tools}
    assert tool_names == {
        "lyra_download_result",
        "lyra_get_job_result",
        "lyra_search_metrics",
        "lyra_get_metric",
        "lyra_get_result_metadata",
        "lyra_get_result_preview",
        "lyra_list_metrics",
        "lyra_lookup_met_zone",
        "lyra_run_metric",
    }
    run_tool = next(tool for tool in tools.tools if tool.name == "lyra_run_metric")
    assert "do not rerun" in (run_tool.description or "")
    assert "lyra_get_job_result" in (run_tool.description or "")
    wait_contracts = {
        tool.name: tool.inputSchema["properties"]["wait_seconds"]
        for tool in tools.tools
        if tool.name in {"lyra_run_metric", "lyra_get_job_result"}
    }
    assert wait_contracts == {
        "lyra_run_metric": {
            "default": 2,
            "description": (
                "Maximum time to wait for a terminal result, in seconds. "
                "Must be between 0 and 10 inclusive; defaults to 2."
            ),
            "maximum": 10.0,
            "minimum": 0.0,
            "title": "Wait Seconds",
            "type": "number",
        },
        "lyra_get_job_result": {
            "default": 30.0,
            "description": (
                "Maximum time to wait for a terminal result, in seconds. "
                "Must be between 0 and 30 inclusive; defaults to 30."
            ),
            "maximum": 30.0,
            "minimum": 0.0,
            "title": "Wait Seconds",
            "type": "number",
        },
    }
    for tool in tools.tools:
        assert tool.inputSchema["type"] == "object"
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema is not None
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is False
        if tool.name == "lyra_run_metric":
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.idempotentHint is False
            assert tool.annotations.openWorldHint is True
        else:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.idempotentHint is True
            assert tool.annotations.openWorldHint is False
    assert called.isError is False
    assert called.structuredContent is not None
    assert called.structuredContent["name"] == "smoke_table_metric"
    assert lookup.isError is False
    assert lookup.structuredContent == {
        "cve_met": "09.01",
        "nom_met": "Valle de México",
    }
    assert extra_argument.isError is True
    assert extra_argument.structuredContent is not None
    assert extra_argument.structuredContent["error"]["code"] == "invalid_arguments"
    assert invalid_type.isError is True
    assert invalid_type.structuredContent is not None
    assert invalid_type.structuredContent["error"]["code"] == "invalid_arguments"
    assert unknown.isError is True
    assert unknown.structuredContent is not None
    assert unknown.structuredContent["error"]["code"] == "unknown_tool"


def test_streamable_http_transport_enforces_sdk_request_rules() -> None:
    client = _ManagedTestClient(create_mcp_app(agent_api_key="agent-secret"))

    invalid_origin = client.post(
        "/",
        json=_initialize_payload(),
        headers={**_mcp_headers(), "Origin": "https://attacker.example"},
    )
    invalid_protocol = client.post(
        "/",
        json=validate_json_object({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        headers={**_mcp_headers(), "MCP-Protocol-Version": "1900-01-01"},
    )
    notification = client.post(
        "/",
        json=validate_json_object(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ),
        headers=_mcp_headers(),
    )
    invalid_content_type = client.post(
        "/",
        content="{}",
        headers={**_mcp_headers(), "Content-Type": "text/plain"},
    )
    invalid_get_accept = client.get(
        "/",
        headers={**_mcp_headers(), "Accept": "application/json"},
    )

    assert invalid_origin.status_code == 403
    assert invalid_protocol.status_code == 400
    assert "Unsupported protocol version" in invalid_protocol.text
    assert notification.status_code == 202
    assert invalid_content_type.status_code == 400
    assert invalid_get_accept.status_code == 406


def test_streamable_http_transport_allows_public_api_host() -> None:
    client = _ManagedTestClient(
        _create_mcp_app(
            agent_api_key="agent-secret",
            public_api_base_url="https://lyra.example.test/api",
        )
    )

    public_host = client.post(
        "/",
        json=_initialize_payload(),
        headers={**_mcp_headers(), "Host": "lyra.example.test"},
    )
    public_host_with_port = client.post(
        "/",
        json=_initialize_payload(),
        headers={**_mcp_headers(), "Host": "lyra.example.test:443"},
    )
    unexpected_host = client.post(
        "/",
        json=_initialize_payload(),
        headers={**_mcp_headers(), "Host": "attacker.example"},
    )

    assert public_host.status_code == 200
    assert public_host_with_port.status_code == 200
    assert unexpected_host.status_code == 421


def test_mcp_search_metrics_ranks_public_catalog_candidates() -> None:
    backend = FakeMCPBackend(
        [
            _file_metric("smoke_file_metric", "Write a small text artifact."),
            _table_metric(
                "accessibility_score",
                "Measure access to clinics and services.",
            ),
            _table_metric("population_count", "Count residents by area."),
        ]
    )
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_search_metrics",
            {"query": "clinic accessibility value", "limit": 2},
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert payload["catalog_fingerprint"] == "catalog-1"
    assert [candidate["metric"] for candidate in payload["candidates"]] == [
        "accessibility_score",
        "population_count",
    ]
    first = payload["candidates"][0]
    assert first["required_spatial_fields"] == [
        {"field": "location", "kind": "location"}
    ]
    assert first["output_kind"] == "table"
    assert first["relevant_columns"][0]["name"] == "value"
    assert "Matches" in first["reason"]


def test_mcp_search_metrics_does_not_treat_tokenless_query_as_inventory() -> None:
    client = _ManagedTestClient(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend([_table_metric("population", "Residents.")]),
        )
    )

    payload = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload("lyra_search_metrics", {"query": "?!"}),
            headers=_mcp_headers(),
        )
    )

    assert payload["candidates"] == []


@pytest.mark.parametrize(("limit", "suggested"), [(0, 1), (100, 20)])
@pytest.mark.parametrize(
    ("tool_name", "base_arguments"),
    [
        ("lyra_search_metrics", {"query": "population"}),
        ("lyra_list_metrics", {}),
    ],
)
def test_metric_discovery_limit_errors_include_correction(
    tool_name: str,
    base_arguments: dict[str, Any],
    limit: int,
    suggested: int,
) -> None:
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([]))
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            tool_name,
            {**base_arguments, "limit": limit},
        ),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    error = result["structuredContent"]["error"]
    assert result["isError"] is True
    assert error["code"] == "invalid_arguments"
    assert error["details"]["allowed_bounds"] == {
        "limit": {"minimum": 1, "maximum": 20}
    }
    assert error["details"]["suggested_arguments"] == {
        **base_arguments,
        "limit": suggested,
    }


def test_metric_discovery_contracts_route_inventory_and_search() -> None:
    list_contract = TOOL_CONTRACTS_BY_NAME["lyra_list_metrics"]
    search_contract = TOOL_CONTRACTS_BY_NAME["lyra_search_metrics"]

    assert "explicitly asks" in list_contract.description
    assert "task-specific" in search_contract.description
    assert search_contract.input_schema["properties"]["limit"]["maximum"] == 20
    assert (
        "Usually omit"
        in search_contract.input_schema["properties"]["limit"]["description"]
    )
    assert list_contract.input_schema["properties"]["limit"]["default"] == 20


def test_mcp_list_metrics_returns_compact_paginated_inventory() -> None:
    long_description = f"  First\nmetric  {'x' * 300}  "
    metrics = [
        _table_metric(
            f"metric_{index:02d}",
            long_description if index == 0 else f"Metric {index}.",
        )
        for index in reversed(range(22))
    ]
    client = _ManagedTestClient(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend(metrics),
        )
    )

    first = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload("lyra_list_metrics", {}),
            headers=_mcp_headers(),
        )
    )
    assert first["catalog_fingerprint"] == "catalog-1"
    assert first["total_count"] == 22
    assert [metric["name"] for metric in first["metrics"]] == [
        f"metric_{index:02d}" for index in range(20)
    ]
    assert len(first["metrics"][0]["description"]) == 240
    assert "\n" not in first["metrics"][0]["description"]
    assert first["metrics"][0]["description"].endswith("…")
    assert first["next_cursor"]

    second = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_list_metrics",
                {"cursor": first["next_cursor"], "limit": 1},
            ),
            headers=_mcp_headers(),
        )
    )
    assert [metric["name"] for metric in second["metrics"]] == ["metric_20"]
    assert second["next_cursor"]

    third = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_list_metrics",
                {"cursor": second["next_cursor"]},
            ),
            headers=_mcp_headers(),
        )
    )
    assert [metric["name"] for metric in third["metrics"]] == ["metric_21"]
    assert third["next_cursor"] is None


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor!",
        _metric_cursor_payload(version=2),
        _metric_cursor_payload(offset=0),
        _metric_cursor_payload(offset=2),
    ],
)
def test_mcp_list_metrics_rejects_invalid_cursors(cursor: str) -> None:
    client = _ManagedTestClient(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend([_table_metric("metric", "A metric.")]),
        )
    )

    response = client.post(
        "/",
        json=_tool_call_payload("lyra_list_metrics", {"cursor": cursor}),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "invalid_cursor"
    assert "without cursor" in result["structuredContent"]["error"]["details"]["action"]


def test_mcp_list_metrics_rejects_cursor_after_catalog_change() -> None:
    backend = FakeMCPBackend(
        [
            _table_metric("metric_a", "First metric."),
            _table_metric("metric_b", "Second metric."),
        ]
    )
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    first = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload("lyra_list_metrics", {"limit": 1}),
            headers=_mcp_headers(),
        )
    )
    backend.catalog = backend.catalog.model_copy(
        update={"catalog_fingerprint": "catalog-2"}
    )

    response = client.post(
        "/",
        json=_tool_call_payload("lyra_list_metrics", {"cursor": first["next_cursor"]}),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    details = result["structuredContent"]["error"]["details"]
    assert details["reason"] == "The metric catalog changed between pages."


def test_mcp_list_metrics_handles_empty_and_exact_boundary_catalogs() -> None:
    empty_client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([]))
    )
    empty = _tool_payload(
        empty_client.post(
            "/",
            json=_tool_call_payload("lyra_list_metrics", {}),
            headers=_mcp_headers(),
        )
    )
    assert empty["total_count"] == 0
    assert empty["metrics"] == []
    assert empty["next_cursor"] is None

    exact_client = _ManagedTestClient(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend(
                [
                    _table_metric("metric_b", "Second."),
                    _table_metric("metric_a", "First."),
                ]
            ),
        )
    )
    exact = _tool_payload(
        exact_client.post(
            "/",
            json=_tool_call_payload("lyra_list_metrics", {"limit": 2}),
            headers=_mcp_headers(),
        )
    )
    assert [metric["name"] for metric in exact["metrics"]] == [
        "metric_a",
        "metric_b",
    ]
    assert exact["next_cursor"] is None


def test_official_client_supports_met_zone_lookup_and_normalized_discovery() -> None:
    backend = FakeMCPBackend(
        [
            _table_metric(
                "tree_coverage",
                "Cobertura de árboles urbanos.",
            ),
            _table_metric(
                "populationDensity",
                "Population density by neighborhood.",
            ),
            _table_metric(
                "heat-risk-index",
                "Heat risk by neighborhood.",
            ),
        ]
    )
    mcp_app = create_mcp_app(agent_api_key="agent-secret", backend=backend)
    mounted_app = Starlette(routes=[Mount("/mcp", app=mcp_app)])

    async def exercise_discovery() -> tuple[Any, ...]:
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mounted_app),
                base_url="http://testserver",
                headers=_mcp_headers(),
                follow_redirects=True,
            ) as http_client,
            streamable_http_client(
                "http://testserver/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            canonical = await session.call_tool(
                "lyra_lookup_met_zone",
                {"name": "Mexico City"},
            )
            fuzzy = await session.call_tool(
                "lyra_lookup_met_zone",
                {"name": "Mexcio City"},
            )
            missing = await session.call_tool(
                "lyra_lookup_met_zone",
                {"name": "Atlantis"},
            )
            snake = await session.call_tool(
                "lyra_search_metrics",
                {"query": "tree coverage"},
            )
            kebab = await session.call_tool(
                "lyra_search_metrics",
                {"query": "heat risk"},
            )
            camel = await session.call_tool(
                "lyra_search_metrics",
                {"query": "population density"},
            )
            accent = await session.call_tool(
                "lyra_search_metrics",
                {"query": "arboles"},
            )
            repeated = await session.call_tool(
                "lyra_search_metrics",
                {"query": "value value"},
            )
            repeated_again = await session.call_tool(
                "lyra_search_metrics",
                {"query": "value value"},
            )
            single = await session.call_tool(
                "lyra_search_metrics",
                {"query": "value"},
            )
            return (
                tools,
                canonical,
                fuzzy,
                missing,
                snake,
                kebab,
                camel,
                accent,
                repeated,
                repeated_again,
                single,
            )

    (
        tools,
        canonical,
        fuzzy,
        missing,
        snake,
        kebab,
        camel,
        accent,
        repeated,
        repeated_again,
        single,
    ) = asyncio.run(exercise_discovery())

    expected_lookup = {"cve_met": "09.01", "nom_met": "Valle de México"}
    assert canonical.structuredContent == expected_lookup
    assert fuzzy.structuredContent == expected_lookup
    assert backend.met_zone_queries == ["Mexico City", "Mexcio City", "Atlantis"]

    assert missing.isError is True
    assert missing.structuredContent is not None
    error = missing.structuredContent["error"]
    assert error["code"] == "unknown_met_zone"
    assert error["details"] == {
        "name": "Atlantis",
        "action": "Revise name and call lyra_lookup_met_zone again.",
    }

    for result, expected_metric in (
        (snake, "tree_coverage"),
        (kebab, "heat-risk-index"),
        (camel, "populationDensity"),
        (accent, "tree_coverage"),
    ):
        assert result.isError is False
        assert result.structuredContent is not None
        assert result.structuredContent["candidates"][0]["metric"] == expected_metric
        assert (
            "public metric contract"
            in result.structuredContent["candidates"][0]["reason"]
        )

    assert repeated.structuredContent is not None
    assert repeated_again.structuredContent is not None
    assert single.structuredContent is not None
    repeated_candidates = repeated.structuredContent["candidates"]
    assert repeated_candidates == repeated_again.structuredContent["candidates"]
    assert repeated_candidates == single.structuredContent["candidates"]
    assert [candidate["metric"] for candidate in repeated_candidates] == [
        "heat-risk-index",
        "populationDensity",
        "tree_coverage",
    ]

    lookup_tool = next(
        tool for tool in tools.tools if tool.name == "lyra_lookup_met_zone"
    )
    search_tool = next(
        tool for tool in tools.tools if tool.name == "lyra_search_metrics"
    )
    for tool in (lookup_tool, search_tool):
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema is not None
        assert tool.outputSchema["additionalProperties"] is False
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False


def test_mcp_get_metric_returns_public_contract() -> None:
    metric = _table_metric("smoke_table_metric", "Return a table.")
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([metric]))
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_get_metric",
            {"metric": "smoke_table_metric"},
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert payload["name"] == "smoke_table_metric"
    assert payload["spatial_inputs"] == {"location": "location"}
    assert payload["output"]["kind"] == "table"


def test_mcp_run_metric_translates_location_met_zone_and_returns_descriptor() -> None:
    backend = FakeMCPBackend([_table_metric("smoke_table_metric", "Return a table.")])
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "smoke_table_metric",
                "met_zone_code": "09.01",
                "parameters": {"value": 7},
                "wait_seconds": 1,
            },
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert backend.payloads[0] == {
        "value": 7,
        "location": {"data_type": "met_zone_code", "value": "09.01"},
    }
    assert payload["job_id"] == "job-1"
    assert payload["status"] == "succeeded"
    assert payload["result_ref"] == "lyra://results/job-1"
    assert payload["preview"]["rows"] == [{"_result_index": "area-1", "value": 7}]
    assert payload["reused"] is False


def test_mcp_run_metric_translates_bounds_met_zone() -> None:
    backend = FakeMCPBackend(
        [
            _table_metric(
                "smoke_bounds_metric",
                "Return a bounds table.",
                spatial_inputs={"bounds": "bounds"},
            )
        ]
    )
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "smoke_bounds_metric",
                "met_zone_code": "13.02",
                "parameters": {"value": 3},
                "wait_seconds": 1,
            },
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert payload["status"] == "succeeded"
    assert backend.payloads[0] == {
        "value": 3,
        "bounds": {"data_type": "met_zone_code", "value": "13.02"},
    }


def test_mcp_run_metric_returns_running_continuation_when_wait_expires() -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["queued"]
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "slow_metric",
                "met_zone_code": "09.01",
                "parameters": {"value": 7},
                "wait_seconds": 0,
            },
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert payload == {
        "status": "running",
        "job_id": "job-1",
        "result_ref": "lyra://results/job-1",
        "poll_after_seconds": 1,
        "next_tool": "lyra_get_job_result",
        "reused": False,
    }


def test_mcp_run_metric_reuses_idempotent_submission() -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["queued"]
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    arguments = {
        "metric": "slow_metric",
        "met_zone_code": "09.01",
        "parameters": {"value": 7},
        "idempotency_key": "retry-key",
        "wait_seconds": 0,
    }

    first = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload("lyra_run_metric", arguments),
            headers=_mcp_headers(),
        )
    )
    replay = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload("lyra_run_metric", arguments),
            headers=_mcp_headers(),
        )
    )

    assert first["job_id"] == replay["job_id"] == "job-1"
    assert first["reused"] is False
    assert replay["reused"] is True
    assert [payload for payload in backend.payloads if "_poll" not in payload] == [
        {
            "value": 7,
            "location": {"data_type": "met_zone_code", "value": "09.01"},
        }
    ]


def test_mcp_run_metric_reports_idempotency_conflict() -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["queued"]
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    base = {
        "metric": "slow_metric",
        "met_zone_code": "09.01",
        "idempotency_key": "conflict-key",
        "wait_seconds": 0,
    }
    client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {**base, "parameters": {"value": 7}},
        ),
        headers=_mcp_headers(),
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {**base, "parameters": {"value": 8}},
        ),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"] == {
        "code": "idempotency_conflict",
        "message": "The idempotency key is already bound to a different request.",
        "details": {"idempotency_key": "conflict-key", "job_id": "job-1"},
    }


def test_mcp_run_metric_reports_structured_rate_limit_retry_metadata() -> None:
    class RateLimitedBackend(FakeMCPBackend):
        async def create_job(
            self,
            metric: str,
            payload: dict[str, Any],
            *,
            idempotency_key: str | None = None,
        ) -> JobCreateResponse:
            del metric, payload, idempotency_key
            code = "rate_limited"
            message = "Agent job submission limit exceeded. Please try again later."
            raise self._tool_error(
                code,
                message,
                {"retry_after_seconds": 17},
            )

    backend = RateLimitedBackend([_table_metric("slow_metric", "Return later.")])
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "slow_metric",
                "met_zone_code": "09.01",
                "parameters": {"value": 7},
                "wait_seconds": 0,
            },
        ),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"] == {
        "code": "rate_limited",
        "message": "Agent job submission limit exceeded. Please try again later.",
        "details": {"retry_after_seconds": 17},
    }


def test_mcp_get_job_result_polls_from_running_to_succeeded() -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["queued", "succeeded"]
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    run_response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "slow_metric",
                "met_zone_code": "09.01",
                "parameters": {"value": 9},
                "wait_seconds": 0,
            },
        ),
        headers=_mcp_headers(),
    )
    running = _tool_payload(run_response)

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_get_job_result",
            {"result_ref": running["result_ref"], "wait_seconds": 1},
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert payload["status"] == "succeeded"
    assert payload["result_ref"] == "lyra://results/job-1"
    assert payload["preview"]["rows"] == [{"_result_index": "area-1", "value": 9}]


def test_mcp_get_job_result_returns_running_continuation() -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["running"]
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    backend.jobs["job-1"] = JobStatusInfo(
        job_id="job-1",
        status="running",
        updated_at=datetime.now(UTC),
        metric="slow_metric",
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_get_job_result",
            {"result_ref": "lyra://results/job-1", "wait_seconds": 0},
        ),
        headers=_mcp_headers(),
    )

    assert _tool_payload(response) == {
        "status": "running",
        "job_id": "job-1",
        "result_ref": "lyra://results/job-1",
        "poll_after_seconds": 1,
        "next_tool": "lyra_get_job_result",
    }


def test_mcp_result_metadata_preview_and_download_tools_are_compact() -> None:
    backend = FakeMCPBackend([_table_metric("smoke_table_metric", "Return a table.")])
    provenance = JobRunProvenance.model_validate(
        {
            "metric": "smoke_table_metric",
            "catalog_fingerprint": "catalog-1",
            "plugin": {"name": "smoke-plugin", "version": "1.2.3"},
            "input": {
                "location": {"data_type": "met_zone_code", "value": "09.01"},
                "value": 6,
            },
            "output": backend.catalog.metrics[0].output,
            "created_at": "2026-07-09T12:00:00Z",
            "row_identity": {
                "field": "cvegeo",
                "namespace": "inegi:cvegeo:ageb",
                "version": "2020",
            },
        }
    )
    descriptor = build_result_descriptor(
        TableJobResult(
            job_id="job-1",
            index=["area-1", "area-2"],
            columns=["value"],
            data=[[6], [8]],
        ),
        lifetime=ResultLifetime(
            expires_in_seconds=3600,
            expires_at=datetime(2026, 7, 9, 13, 5, tzinfo=UTC),
        ),
        completed_at=_COMPLETED_AT,
        provenance=provenance,
    )
    backend.jobs["job-1"] = JobStatusInfo(
        job_id="job-1",
        status="succeeded",
        updated_at=datetime.now(UTC),
        metric="smoke_table_metric",
    )
    backend.descriptors["job-1"] = descriptor
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    metadata = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_get_result_metadata",
                {"result_ref": "lyra://results/job-1"},
                request_id=20,
            ),
            headers=_mcp_headers(),
        )
    )
    preview = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_get_result_preview",
                {"result_ref": "lyra://results/job-1"},
                request_id=21,
            ),
            headers=_mcp_headers(),
        )
    )
    download = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_download_result",
                {"result_ref": "lyra://results/job-1"},
                request_id=22,
            ),
            headers={
                **_mcp_headers(),
                "Forwarded": "host=proxy-only.internal;proto=http",
                "X-Forwarded-Host": "proxy-only.internal",
                "X-Forwarded-Proto": "http",
            },
        )
    )

    assert metadata == {
        "schema_version": 1,
        "job_id": "job-1",
        "status": "succeeded",
        "result_kind": "table",
        "result_ref": "lyra://results/job-1",
        "provenance": provenance.model_dump(mode="json", exclude_none=True),
        "completed_at": "2026-07-09T12:05:00Z",
        "lifetime": {
            "expires_in_seconds": 3600,
            "expires_at": "2026-07-09T13:05:00Z",
        },
        "table": {
            "row_count": 2,
            "column_count": 1,
            "columns": ["value"],
            "column_contracts": [
                {
                    "name": "value",
                    "type": "integer",
                    "unit": "count",
                    "description": "Submitted value.",
                    "nullable": False,
                }
            ],
            "index_field": "_result_index",
            "row_identity": {
                "field": "cvegeo",
                "namespace": "inegi:cvegeo:ageb",
                "version": "2020",
            },
        },
        "file": None,
        "summary": metadata["summary"],
        "error": None,
    }
    assert "preview" not in metadata
    assert preview["preview"]["rows"] == [
        {"_result_index": "area-1", "value": 6},
        {"_result_index": "area-2", "value": 8},
    ]
    assert preview["schema_version"] == 1
    assert preview["provenance"]["plugin"]["version"] == "1.2.3"
    assert preview["completed_at"] == "2026-07-09T12:05:00Z"
    assert "raw" not in preview
    assert download == {
        "job_id": "job-1",
        "result_ref": "lyra://results/job-1",
        "status": "succeeded",
        "format": "jsonl",
        "media_type": "application/x-ndjson",
        "lyra_api": {
            "method": "GET",
            "url": ("https://lyra.example.test/api/jobs/job-1/result/table.jsonl"),
            "authentication": {
                "scheme": "Bearer",
                "credential_env_var": "LYRA_AGENT_API_KEY",
            },
        },
        "client_helpers": {
            "python_sync": (
                "LyraClient.results.download(result_ref, path, format='jsonl')"
            ),
            "python_async": (
                "await AsyncLyraClient.results.download("
                "result_ref, path, format='jsonl')"
            ),
        },
        "expires_in_seconds": 3600,
        "expires_at": "2026-07-09T13:05:00Z",
    }
    assert "agent-secret" not in json.dumps(download)
    assert "proxy-only" not in json.dumps(download)
    assert "?" not in download["lyra_api"]["url"]
    assert "#" not in download["lyra_api"]["url"]


def test_mcp_download_returns_structured_expired_error() -> None:
    client = _ManagedTestClient(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend([_table_metric("metric", "Return a table.")]),
        )
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_download_result",
            {"result_ref": "lyra://results/job-expired"},
        ),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "result_expired"
    assert error["details"] == {
        "job_id": "job-expired",
        "result_ref": "lyra://results/job-expired",
        "rerun_required": True,
    }
    assert "Rerun the metric" in error["message"]


@pytest.mark.parametrize(
    "result",
    [
        FileJobResult(
            job_id="job-file",
            file_path="/lyra_data/internal/result.tif",
            media_type="image/tiff",
        ),
        FailedJobResult(
            job_id="job-failed",
            error={"type": "runtime_error", "message": "boom"},
        ),
        CancelledJobResult(job_id="job-cancelled"),
    ],
)
def test_mcp_download_preserves_structured_result_kind_errors(
    result: FileJobResult | FailedJobResult | CancelledJobResult,
) -> None:
    backend = FakeMCPBackend([_table_metric("metric", "Return a table.")])
    backend.descriptors[result.job_id] = build_result_descriptor(
        result,
        completed_at=_COMPLETED_AT,
    )
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_download_result",
            {"result_ref": f"lyra://results/{result.job_id}"},
        ),
        headers=_mcp_headers(),
    )

    tool_result = response.json()["result"]
    assert tool_result["isError"] is True
    error = tool_result["structuredContent"]["error"]
    assert error["code"] == "unsupported_result_download"
    assert error["details"]["result_kind"] == result.kind
    assert "/lyra_data/" not in json.dumps(error)


def test_mcp_get_job_result_returns_failed_and_cancelled_envelopes() -> None:
    backend = FakeMCPBackend([_table_metric("metric", "Return a table.")])
    failed_descriptor = build_result_descriptor(
        FailedJobResult(
            job_id="job-failed",
            error={"type": "runtime_error", "message": "boom"},
        ),
        completed_at=_COMPLETED_AT,
    )
    cancelled_descriptor = build_result_descriptor(
        CancelledJobResult(job_id="job-x"),
        completed_at=_COMPLETED_AT,
    )
    backend.descriptors["job-failed"] = failed_descriptor
    backend.descriptors["job-x"] = cancelled_descriptor
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    failed = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_get_job_result",
                {"result_ref": "lyra://results/job-failed", "wait_seconds": 0},
                request_id=30,
            ),
            headers=_mcp_headers(),
        )
    )
    cancelled = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_get_job_result",
                {"result_ref": "lyra://results/job-x", "wait_seconds": 0},
                request_id=31,
            ),
            headers=_mcp_headers(),
        )
    )

    assert failed["status"] == "failed"
    assert failed["result_kind"] == "failed"
    assert failed["error"] == {"type": "runtime_error", "message": "boom"}
    assert cancelled["status"] == "cancelled"
    assert cancelled["result_kind"] == "cancelled"
    assert cancelled["preview"]["rows"] == []


def test_mcp_result_tools_reject_invalid_result_ref() -> None:
    client = _ManagedTestClient(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend([_table_metric("metric", "Return a table.")]),
        )
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_get_result_metadata",
            {"result_ref": "https://example.test/results/job-1"},
        ),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "lyra_run_metric",
            {
                "metric": "slow_metric",
                "met_zone_code": "09.01",
                "wait_seconds": -0.01,
            },
        ),
        (
            "lyra_run_metric",
            {
                "metric": "slow_metric",
                "met_zone_code": "09.01",
                "wait_seconds": 10.01,
            },
        ),
        (
            "lyra_get_job_result",
            {"result_ref": "lyra://results/job-1", "wait_seconds": -0.01},
        ),
        (
            "lyra_get_job_result",
            {"result_ref": "lyra://results/job-1", "wait_seconds": 30.01},
        ),
    ],
)
def test_mcp_wait_ranges_are_rejected_before_polling(
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["running"]
    backend.jobs["job-1"] = JobStatusInfo(
        job_id="job-1",
        status="running",
        updated_at=datetime.now(UTC),
        metric="slow_metric",
    )
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(tool_name, arguments),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "invalid_arguments"
    assert backend.payloads == []


@pytest.mark.parametrize("wait_seconds", [float("nan"), float("inf"), -float("inf")])
def test_mcp_non_finite_waits_are_rejected_before_polling(
    wait_seconds: float,
) -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["running"]
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    request = _tool_call_payload(
        "lyra_run_metric",
        {
            "metric": "slow_metric",
            "met_zone_code": "09.01",
            "wait_seconds": wait_seconds,
        },
    )

    response = client.post(
        "/",
        content=json.dumps(request),
        headers={**_mcp_headers(), "Content-Type": "application/json"},
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "invalid_arguments"
    assert backend.payloads == []


def test_mcp_wait_boundaries_are_accepted() -> None:
    backend = FakeMCPBackend([_table_metric("metric", "Return a table.")])
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    run_result = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_run_metric",
                {
                    "metric": "metric",
                    "met_zone_code": "09.01",
                    "wait_seconds": 10,
                },
            ),
            headers=_mcp_headers(),
        )
    )
    poll_result = _tool_payload(
        client.post(
            "/",
            json=_tool_call_payload(
                "lyra_get_job_result",
                {
                    "result_ref": run_result["result_ref"],
                    "wait_seconds": 30,
                },
            ),
            headers=_mcp_headers(),
        )
    )

    assert run_result["status"] == "succeeded"
    assert poll_result["status"] == "succeeded"


def test_mcp_run_metric_surfaces_unknown_metric_as_tool_error() -> None:
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([]))
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {"metric": "missing", "met_zone_code": "09.01"},
        ),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "unknown_metric"


def test_mcp_run_metric_surfaces_invalid_parameters_as_tool_error() -> None:
    backend = FakeMCPBackend(
        [_table_metric("smoke_table_metric", "Return a table.", value_type="integer")]
    )
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "smoke_table_metric",
                "met_zone_code": "09.01",
                "parameters": {"value": "invalid"},
            },
        ),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "invalid_parameters"
    assert error["details"][0]["loc"] == ["value"]


def test_mcp_run_metric_rejects_unsupported_spatial_shapes() -> None:
    backend = FakeMCPBackend(
        [
            _table_metric(
                "multi_spatial_metric",
                "Needs multiple shapes.",
                spatial_inputs={"location": "location", "bounds": "bounds"},
            )
        ]
    )
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "multi_spatial_metric",
                "met_zone_code": "09.01",
                "parameters": {"value": 1},
            },
        ),
        headers=_mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "unsupported_spatial_shape"


def test_main_mounts_mcp_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LYRA_AGENT_API_KEY", "agent-secret")
    config = load_test_config(tmp_path)
    config.mcp.enabled = True
    client = _app_with_mcp(config, monkeypatch)

    response = client.post(
        "/mcp/",
        json=_initialize_payload(),
        headers=_mcp_headers(),
    )

    assert response.status_code == 200
    assert response.json()["result"]["instructions"] == SERVER_INSTRUCTIONS


def test_main_mcp_mount_requires_dedicated_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LYRA_AGENT_API_KEY", "agent-secret")
    config = load_test_config(tmp_path)
    config.mcp.enabled = True
    client = _app_with_mcp(config, monkeypatch)

    missing = client.post("/mcp/", json=_initialize_payload())
    admin_token = client.post(
        "/mcp/",
        json=_initialize_payload(),
        headers=_mcp_headers("admin-secret"),
    )

    assert missing.status_code == 401
    assert admin_token.status_code == 403


def test_main_does_not_mount_mcp_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    client = _app_with_mcp(config, monkeypatch)

    response = client.post(
        "/mcp/",
        json=_initialize_payload(),
        headers=_mcp_headers(),
    )

    assert response.status_code == 404
