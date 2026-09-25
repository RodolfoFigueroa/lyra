from __future__ import annotations

import asyncio
import base64
import json
import threading
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, Unpack
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import Request
from jsonschema import validate
from lyra.sdk.models.job import (
    CancelledJobResult,
    FailedJobResult,
    FileJobResult,
    JobCreateResponse,
    JobLifecycleStatus,
    JobLinks,
    JobProgress,
    JobRunProvenance,
    JobStatusInfo,
    ResultLifetime,
    TableJobResult,
)
from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfo
from lyra.sdk.models.plugin import (
    FileOutput,
    SpatialInputKind,
    TableColumn,
    TableOutput,
)
from lyra.sdk.types import (
    JsonObject,
    JsonValue,
    validate_json_object,
    validate_json_value,
)
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra_app import main, mcp, registry
from lyra_app.db.dependencies import get_database_runtime
from lyra_app.job_observation import JobObservation
from lyra_app.mcp import SERVER_INSTRUCTIONS, tools
from lyra_app.mcp import create_mcp_app as _create_mcp_app
from lyra_app.mcp.models import TOOL_CONTRACTS_BY_NAME, GetJobResultInput
from lyra_app.mcp.server import ToolCallError
from lyra_app.mcp.tools import InProcessLyraBackend
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

    monkeypatch.setattr(registry, "ensure_catalog_loaded", lambda: None)

    def noop_start() -> None:
        return None

    async def noop_stop() -> None:  # ruff: ignore[unused-async] -- lifecycle double
        return None

    monkeypatch.setattr(main, "start_worker_inspect_collector", noop_start)
    monkeypatch.setattr(main, "stop_worker_inspect_collector", noop_stop)
    return _ManagedTestClient(main.create_app(config))


class FakeMCPBackend:
    def __init__(self, metrics: list[MetricInfo]) -> None:
        self.catalog = MetricCatalogResponse(
            client_schema_version=1,
            json_schema_dialect="https://json-schema.org/draft/2020-12/schema",
            catalog_fingerprint="catalog-1",
            metrics=metrics,
        )
        self.jobs: dict[str, JobStatusInfo] = {}
        self.observations: dict[str, JobObservation] = {}
        self.observed: list[str] = []
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

    async def get_metric(self, metric: str) -> MetricInfo | None:
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
        if payload.get("parameters", {}).get("value") == "invalid":
            code = "invalid_parameters"
            message = "Invalid metric parameters."
            details = [
                {
                    "loc": ["parameters", "value"],
                    "msg": "Expected integer.",
                    "type": "type",
                }
            ]
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
        self.jobs[job_id] = self.job_status(job_id, self.job_status_sequence[0], metric)
        self.observations[job_id] = JobObservation(snapshot=self.jobs[job_id])
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
                result=f"/jobs/{job_id}/result",
            ),
        )

    async def observe_job(self, job_id: str) -> JobObservation:
        self.observed.append(job_id)
        return self.observations.get(job_id, JobObservation())

    @staticmethod
    def job_status(
        job_id: str,
        status: JobLifecycleStatus,
        metric: str | None,
    ) -> JobStatusInfo:
        return JobStatusInfo(
            job_id=job_id,
            status=status,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            metric=metric,
        )

    @staticmethod
    def _tool_error(code: str, message: str, details: JsonValue) -> Exception:
        return ToolCallError(code, message, details)


def _table_metric(
    name: str,
    description: str,
    *,
    spatial_inputs: dict[str, SpatialInputKind] | None = None,
    value_type: str = "integer",
) -> MetricInfo:
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
    properties["parameters"] = {
        "type": "object",
        "properties": {
            "value": {
                "type": value_type,
                "description": "Value copied into each output row.",
            }
        },
        "required": ["value"],
        "additionalProperties": False,
    }
    return MetricInfo(
        name=name,
        description=description,
        request_schema={
            "type": "object",
            "properties": properties,
            "required": [*spatial, "parameters"],
            "additionalProperties": False,
        },
        spatial_inputs=spatial,
        output=TableOutput(
            kind="table",
            columns=[
                TableColumn(
                    name="value",
                    type="integer",
                    unit="count",
                    description="Submitted value.",
                )
            ],
        ),
    )


def _file_metric(name: str, description: str) -> MetricInfo:
    return MetricInfo(
        name=name,
        description=description,
        request_schema={
            "type": "object",
            "properties": {"location": {"type": "object"}},
            "required": ["location"],
            "additionalProperties": False,
        },
        spatial_inputs={"location": "location"},
        output=FileOutput(kind="file", media_type="text/plain", extensions=[".txt"]),
    )


def test_mcp_package_initializes_with_bearer_auth() -> None:
    app = create_mcp_app(
        agent_api_key="agent-secret",
        backend=FakeMCPBackend([]),
    )
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
    assert "every two seconds" in result["instructions"]


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
        "lyra_list_metrics",
        "lyra_lookup_met_zone",
        "lyra_run_metric",
    }
    run_tool = next(tool for tool in tools.tools if tool.name == "lyra_run_metric")
    assert "Submit once" in (run_tool.description or "")
    assert "lyra_get_job_result" in (run_tool.description or "")
    assert all(
        "wait_seconds" not in tool.inputSchema["properties"] for tool in tools.tools
    )
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
    client = _ManagedTestClient(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend([]),
        )
    )

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
            backend=FakeMCPBackend([]),
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
            calls = (
                ("lyra_lookup_met_zone", {"name": "Mexico City"}),
                ("lyra_lookup_met_zone", {"name": "Mexcio City"}),
                ("lyra_lookup_met_zone", {"name": "Atlantis"}),
                ("lyra_search_metrics", {"query": "tree coverage"}),
                ("lyra_search_metrics", {"query": "heat risk"}),
                ("lyra_search_metrics", {"query": "population density"}),
                ("lyra_search_metrics", {"query": "arboles"}),
                ("lyra_search_metrics", {"query": "value value"}),
                ("lyra_search_metrics", {"query": "value value"}),
                ("lyra_search_metrics", {"query": "value"}),
            )
            results = [
                await session.call_tool(name, arguments) for name, arguments in calls
            ]
            return (tools, *results)

    results = asyncio.run(exercise_discovery())

    expected_lookup = {"cve_met": "09.01", "nom_met": "Valle de México"}
    assert results[1].structuredContent == expected_lookup
    assert results[2].structuredContent == expected_lookup
    assert backend.met_zone_queries == ["Mexico City", "Mexcio City", "Atlantis"]

    assert results[3].isError is True
    assert results[3].structuredContent is not None
    error = results[3].structuredContent["error"]
    assert error["code"] == "unknown_met_zone"
    assert error["details"] == {
        "name": "Atlantis",
        "action": "Revise name and call lyra_lookup_met_zone again.",
    }

    for result, expected_metric in (
        (results[4], "tree_coverage"),
        (results[5], "heat-risk-index"),
        (results[6], "populationDensity"),
        (results[7], "tree_coverage"),
    ):
        assert result.isError is False
        assert result.structuredContent is not None
        assert result.structuredContent["candidates"][0]["metric"] == expected_metric
        assert (
            "public metric contract"
            in result.structuredContent["candidates"][0]["reason"]
        )

    assert results[8].structuredContent is not None
    assert results[9].structuredContent is not None
    assert results[10].structuredContent is not None
    repeated_candidates = results[8].structuredContent["candidates"]
    assert repeated_candidates == results[9].structuredContent["candidates"]
    assert repeated_candidates == results[10].structuredContent["candidates"]
    assert [candidate["metric"] for candidate in repeated_candidates] == [
        "heat-risk-index",
        "populationDensity",
        "tree_coverage",
    ]

    lookup_tool = next(
        tool for tool in results[0].tools if tool.name == "lyra_lookup_met_zone"
    )
    search_tool = next(
        tool for tool in results[0].tools if tool.name == "lyra_search_metrics"
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


def test_mcp_run_metric_translates_location_met_zone_and_returns_submission() -> None:
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
            },
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert backend.payloads[0] == {
        "parameters": {"value": 7},
        "location": {"data_type": "met_zone_code", "value": "09.01"},
    }
    assert payload["job_id"] == "job-1"
    assert "status" not in payload
    assert payload["result_ref"] == "lyra://results/job-1"
    assert payload["reused"] is False
    assert backend.observed == []


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
            },
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert "status" not in payload
    assert backend.payloads[0] == {
        "parameters": {"value": 3},
        "bounds": {"data_type": "met_zone_code", "value": "13.02"},
    }


def test_mcp_run_metric_never_observes_completion() -> None:
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
            },
        ),
        headers=_mcp_headers(),
    )

    payload = _tool_payload(response)
    assert payload == {
        "job_id": "job-1",
        "result_ref": "lyra://results/job-1",
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
    assert backend.observed == []
    assert first["reused"] is False
    assert replay["reused"] is True
    assert [payload for payload in backend.payloads if "_poll" not in payload] == [
        {
            "parameters": {"value": 7},
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
    assert error["details"][0]["loc"] == ["parameters", "value"]


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


def test_main_shares_database_runtime_with_rest_and_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    config.mcp.enabled = True
    monkeypatch.setattr(main, "bootstrap_runtime", lambda _: config)
    monkeypatch.setattr(registry, "initialize_catalog", Mock())
    factory = Mock(wraps=_create_mcp_app)
    monkeypatch.setattr(mcp, "create_mcp_app", factory)

    app = main.create_app(config)

    factory.assert_called_once()
    backend = factory.call_args.kwargs["backend"]
    assert isinstance(backend, InProcessLyraBackend)
    assert backend.database is app.state.database
    request = Request({"type": "http", "app": app})
    assert get_database_runtime(request) is backend.database


def _inspect(
    backend: FakeMCPBackend, job_id: str = "job-1", *, tool: str = "lyra_get_job_result"
) -> dict[str, Any]:
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/",
        json=_tool_call_payload(tool, {"result_ref": f"lyra://results/{job_id}"}),
        headers=_mcp_headers(),
    )
    _tool_payload(response)
    wire = response.json()["result"]
    if not wire["isError"]:
        validate(wire["structuredContent"], TOOL_CONTRACTS_BY_NAME[tool].output_schema)
    return wire


@pytest.mark.parametrize(
    "status", ["queued", "running", "failed", "cancelled", "succeeded"]
)
def test_status_only_observations(status: JobLifecycleStatus) -> None:
    backend = FakeMCPBackend([])
    snapshot = backend.job_status("job-1", status, "metric")
    backend.observations["job-1"] = JobObservation(snapshot=snapshot)
    wire = _inspect(backend)
    payload = wire["structuredContent"]
    assert backend.observed == ["job-1"]
    if status == "succeeded":
        assert wire["isError"] is True
        assert payload["error"]["code"] == "result_unavailable"
    else:
        assert wire["isError"] is False
        assert payload["status"] == status
        if status in {"queued", "running"}:
            assert payload["poll_after_seconds"] == 2
            assert payload["metric"] == "metric"
            assert payload["created_at"] == snapshot.created_at.isoformat().replace(
                "+00:00", "Z"
            )
        else:
            assert payload["completed_at"] is None
            assert payload["summary"] is None


@pytest.mark.parametrize("snapshot_status", [None, "queued", "running"])
def test_retained_result_precedes_missing_or_stale_status(
    snapshot_status: JobLifecycleStatus | None,
) -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        snapshot=backend.job_status("job-1", snapshot_status, "metric")
        if snapshot_status
        else None,
        result=TableJobResult(
            job_id="job-1",
            index=["row"],
            columns=["value"],
            data=[[12345678901234567890]],
        ),
    )
    payload = _inspect(backend)["structuredContent"]
    assert payload["status"] == "succeeded"
    assert payload["completed_at"] is None
    assert payload["provenance"] is None
    assert payload["preview"]["rows"] == [
        {"_result_index": "row", "value": 12345678901234567890}
    ]
    assert (
        payload["descriptor"]["url"]
        == "https://lyra.example.test/api/jobs/job-1/result/descriptor"
    )


@pytest.mark.parametrize("result_type", [FailedJobResult, CancelledJobResult])
def test_execution_failures_are_successful_observations(
    result_type: type[FailedJobResult | CancelledJobResult],
) -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        result=result_type(job_id="job-1", error={"message": "failed"})
    )
    wire = _inspect(backend)
    assert wire["isError"] is False
    assert wire["structuredContent"]["error"]["message"] == "failed"
    download = _inspect(backend, tool="lyra_download_result")
    assert download["isError"] is True
    assert download["structuredContent"]["error"]["code"] == "result_not_downloadable"


@pytest.mark.parametrize("tool", ["lyra_get_job_result", "lyra_download_result"])
def test_unknown_or_expired_reference(tool: str) -> None:
    wire = _inspect(FakeMCPBackend([]), tool=tool)
    assert wire["isError"] is True
    assert wire["structuredContent"]["error"]["code"] == "result_not_found"


@pytest.mark.parametrize(
    "tool",
    [
        "lyra_get_result_metadata",
        "lyra_get_result_preview",
        "lyra_get_job_result",
        "lyra_run_metric",
    ],
)
def test_removed_tools_and_wait_arguments_are_rejected(tool: str) -> None:
    backend = FakeMCPBackend([])
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    arguments = (
        {"result_ref": "lyra://results/job-1", "wait_seconds": 0}
        if tool != "lyra_run_metric"
        else {"metric": "metric", "met_zone_code": "09.01", "wait_seconds": 0}
    )
    payload = _tool_payload(
        client.post(
            "/", json=_tool_call_payload(tool, arguments), headers=_mcp_headers()
        )
    )
    assert payload["error"]["code"] == (
        "unknown_tool"
        if tool in {"lyra_get_result_metadata", "lyra_get_result_preview"}
        else "invalid_arguments"
    )
    assert backend.observed == []
    assert backend.payloads == []


def test_file_and_table_download_handoffs(tmp_path: Path) -> None:
    artifact = tmp_path / "private.bin"
    artifact.write_bytes(b"artifact")
    backend = FakeMCPBackend([])
    for job_id, result, suffix, media_type in [
        (
            "file%ü",
            FileJobResult(
                job_id="file%ü",
                file_path=str(artifact),
                media_type="application/octet-stream",
            ),
            "download",
            "application/octet-stream",
        ),
        (
            "table",
            TableJobResult(job_id="table", index=["r"], columns=["v"], data=[[2]]),
            "table.jsonl",
            "application/x-ndjson",
        ),
    ]:
        backend.observations[job_id] = JobObservation(
            result=result, lifetime=ResultLifetime(expires_in_seconds=60)
        )
        wire = _inspect(backend, job_id, tool="lyra_download_result")
        assert wire["isError"] is False
        payload = wire["structuredContent"]
        assert payload["media_type"] == media_type
        assert payload["lifetime"]["expires_in_seconds"] == 60
        assert payload["lyra_api"]["url"].endswith(suffix)
        assert payload["lyra_api"]["authentication"] == {
            "scheme": "Bearer",
            "credential_env_var": "LYRA_AGENT_API_KEY",
        }
        assert "private.bin" not in json.dumps(wire)
        assert "agent-secret" not in json.dumps(wire)
        assert "python" not in json.dumps(wire)
    assert (
        "file%25%C3%BC"
        in _inspect(backend, "file%ü", tool="lyra_download_result")[
            "structuredContent"
        ]["lyra_api"]["url"]
    )
    file_inspection = _inspect(backend, "file%ü")["structuredContent"]
    assert file_inspection["file"]["media_type"] == "application/octet-stream"
    assert "private.bin" not in json.dumps(file_inspection)
    artifact.unlink()
    assert (
        _inspect(backend, "file%ü", tool="lyra_download_result")["structuredContent"][
            "error"
        ]["code"]
        == "result_unavailable"
    )


def test_active_download_polling_guidance() -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        snapshot=backend.job_status("job-1", "queued", "metric")
    )
    payload = _inspect(backend, tool="lyra_download_result")["structuredContent"]
    assert payload["error"]["code"] == "result_not_ready"
    assert payload["error"]["details"]["poll_after_seconds"] == 2


def test_wide_multibyte_preview_is_bounded_and_consistent() -> None:
    backend = FakeMCPBackend([])
    columns = [f"column_{i}" for i in range(40)]
    backend.observations["job-1"] = JobObservation(
        result=TableJobResult(
            job_id="job-1",
            index=[f"row{i}" for i in range(30)],
            columns=columns,
            data=[["界" * 1000] * 40 for _ in range(30)],
        )
    )
    wire = _inspect(backend)
    assert (
        len(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode())
        <= 65536
    )
    payload = wire["structuredContent"]
    assert payload["table"]["row_count"] == 30
    assert payload["table"]["column_count"] == 40
    assert payload["table"]["columns"] == [
        entry["name"] for entry in payload["summary"]["columns"]
    ]
    assert payload["truncation"]["omitted_rows"] == 30 - len(payload["preview"]["rows"])
    assert payload["truncation"]["omitted_columns"] == 40 - len(
        payload["table"]["columns"]
    )
    assert payload["truncation"]["shortened_strings"] == 200
    for row in payload["preview"]["rows"]:
        assert list(row) == ["_result_index", *payload["table"]["columns"]]
        assert all(len(value) <= 500 for value in row.values())


def test_oversized_identifiers_and_errors_have_small_fallback() -> None:
    backend = FakeMCPBackend([])
    job_id = "x" * 40000
    backend.observations[job_id] = JobObservation(
        result=FailedJobResult(job_id=job_id, error={"message": "error"})
    )
    for identifier in [job_id, "unknown" * 10000]:
        wire = _inspect(backend, identifier)
        assert wire["isError"] is True
        assert wire["structuredContent"]["error"]["code"] == "result_response_too_large"
        assert len(json.dumps(wire).encode()) < 65536


@pytest.mark.parametrize(
    "tool", ["lyra_run_metric", "lyra_get_job_result", "lyra_download_result"]
)
def test_operation_deadlines_and_cancellation(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FakeMCPBackend([_table_metric("metric", "metric")])
    cancelled = []

    async def blocked(*_args: object, **_kwargs: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    method = "create_job" if tool == "lyra_run_metric" else "observe_job"
    operation = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(backend, method, operation)
    arguments = TOOL_CONTRACTS_BY_NAME[tool].input_model.model_validate(
        {"metric": "metric", "met_zone_code": "09.01", "idempotency_key": "original"}
        if tool == "lyra_run_metric"
        else {"result_ref": "lyra://results/job-1"}
    )
    monkeypatch.setattr(tools, "OPERATION_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ToolCallError) as error:
        asyncio.run(
            tools.execute_tool(
                tool, arguments, backend, public_api_base_url="https://example.test"
            )
        )
    assert error.value.code == "operation_timeout"
    assert operation.call_count == 1
    assert cancelled == [True]
    if tool == "lyra_run_metric":
        assert "original idempotency" in str(error.value.details)

    async def externally_cancel() -> None:
        monkeypatch.setattr(tools, "OPERATION_TIMEOUT_SECONDS", 30)
        task = asyncio.create_task(
            tools.execute_tool(
                tool, arguments, backend, public_api_base_url="https://example.test"
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(externally_cancel())


def test_retryable_observation_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeMCPBackend([])
    failure = ToolCallError("backend_error", "unavailable", {"retryable": True})
    operation = AsyncMock(side_effect=failure)
    sleep = AsyncMock()
    monkeypatch.setattr(backend, "observe_job", operation)
    monkeypatch.setattr(tools.asyncio, "sleep", sleep)
    with pytest.raises(ToolCallError) as error:
        asyncio.run(
            tools.execute_tool(
                "lyra_get_job_result",
                GetJobResultInput(result_ref="lyra://results/job-1"),
                backend,
                public_api_base_url="https://example.test",
            )
        )
    assert error.value is failure
    operation.assert_awaited_once()
    sleep.assert_not_called()


def test_compact_provenance_contracts_and_progress_are_schema_valid() -> None:
    backend = FakeMCPBackend([])
    columns = [
        TableColumn(
            name=f"v{i}",
            type="number",
            unit="count",
            description="x" * 600,
            nullable=False,
        )
        for i in range(25)
    ]
    provenance = JobRunProvenance.model_validate(
        {
            "metric": "metric",
            "catalog_fingerprint": "catalog",
            "plugin": {"name": "plugin", "version": "1"},
            "created_at": _COMPLETED_AT,
            "output": TableOutput(kind="table", columns=columns),
            "row_identity": {"field": "cvegeo", "namespace": "inegi"},
            "input": {
                "year": 2026,
                "zones": [1, 2, 3],
                "geometry": {
                    "data_type": "geojson",
                    "value": {
                        "type": "FeatureCollection",
                        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [99, 20]},
                            }
                        ]
                        * 1000,
                    },
                },
            },
        }
    )
    unchanged = provenance.model_dump_json()
    backend.observations["job-1"] = JobObservation(
        provenance=provenance,
        result=TableJobResult(
            job_id="job-1",
            columns=[column.name for column in columns],
            index=["row"],
            data=[[2.5] * 25],
        ),
    )
    payload = _inspect(backend)["structuredContent"]
    assert payload["provenance"]["input"] == {
        "year": 2026,
        "zones": {"type": "array", "count": 3},
        "geometry": {
            "type": "FeatureCollection",
            "feature_count": 1000,
            "crs": "EPSG:4326",
        },
    }
    assert "coordinates" not in json.dumps(payload)
    assert "output" not in payload["provenance"]
    assert [
        column["name"] for column in payload["table"]["column_contracts"]
    ] == payload["table"]["columns"]
    assert payload["table"]["row_identity"]["field"] == "cvegeo"
    assert len(payload["table"]["column_contracts"][0]["description"]) == 500
    assert provenance.model_dump_json() == unchanged
    snapshot = backend.job_status("job-1", "running", "metric")
    snapshot.progress = JobProgress(
        timestamp=_COMPLETED_AT,
        stage="compute",
        current=1,
        total=10,
        message="x" * 1000,
    )
    backend.observations["job-1"] = JobObservation(snapshot=snapshot)
    payload = _inspect(backend)["structuredContent"]
    assert payload["progress"]["current"] == 1
    assert payload["progress"]["total"] == 10
    assert len(payload["progress"]["message"]) == 500
    assert payload["truncation"]["shortened_strings"] == 1


def test_large_errors_and_oversized_table_fields_are_omitted() -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        result=FailedJobResult(
            job_id="job-1",
            error={
                "code": "x" * 50000,
                "message": "界" * 1000,
                "details": ["large"] * 10000,
            },
        )
    )
    wire = _inspect(backend)
    assert wire["isError"] is False
    assert wire["structuredContent"]["error"] is None
    assert "error" in wire["structuredContent"]["truncation"]["omitted_sections"]
    backend.observations["job-1"] = JobObservation(
        result=TableJobResult(
            job_id="job-1", columns=["x" * 1000], index=["y" * 1000], data=[[2]]
        )
    )
    payload = _inspect(backend)["structuredContent"]
    assert payload["table"]["columns"] == []
    assert payload["preview"]["rows"] == []
    assert payload["truncation"]["omitted_columns"] == 1
    assert payload["truncation"]["omitted_rows"] == 1


def test_backend_errors_are_retryable_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeMCPBackend([])
    observe = AsyncMock(side_effect=RedisConnectionError("private backend location"))
    monkeypatch.setattr(backend, "observe_job", observe)
    wire = _inspect(backend)
    assert wire["isError"] is True
    assert wire["structuredContent"]["error"]["details"]["retryable"] is True
    assert "private backend" not in json.dumps(wire)
    observe.assert_awaited_once()


@pytest.mark.parametrize("value", [10**400, 1e308])
def test_large_numeric_values_remain_exact(value: float) -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        result=TableJobResult(
            job_id="job-1", index=["a", "b"], columns=["value"], data=[[value], [value]]
        )
    )
    wire = _inspect(backend)
    assert wire["isError"] is False
    payload = wire["structuredContent"]
    assert payload["preview"]["rows"][0]["value"] == value
    assert payload["truncation"]["omitted_sections"]


def test_budget_trims_column_bundles_in_order() -> None:
    backend = FakeMCPBackend([])
    columns = [
        TableColumn(
            name=f"value_{i}_" + "界" * 450,
            type="number",
            unit="count",
            description="界" * 500,
            nullable=False,
        )
        for i in range(20)
    ]
    provenance = JobRunProvenance.model_validate(
        {
            "metric": "metric",
            "catalog_fingerprint": "catalog",
            "plugin": {"name": "plugin", "version": "1"},
            "created_at": _COMPLETED_AT,
            "output": TableOutput(kind="table", columns=columns),
            "input": {"detail": "x" * 400},
        }
    )
    backend.observations["job-1"] = JobObservation(
        provenance=provenance,
        result=TableJobResult(
            job_id="job-1",
            index=["row"],
            columns=[column.name for column in columns],
            data=[[3] * 20],
        ),
    )
    wire = _inspect(backend)
    payload = wire["structuredContent"]
    assert payload["provenance"]["input"] is None
    assert "provenance.input" in payload["truncation"]["omitted_sections"]
    assert payload["preview"]["rows"] == []
    retained = payload["table"]["columns"]
    assert 0 < len(retained) < 20
    assert retained == [column.name for column in columns[: len(retained)]]
    assert retained == [
        column["name"] for column in payload["table"]["column_contracts"]
    ]
    assert retained == [column["name"] for column in payload["summary"]["columns"]]
    assert payload["truncation"]["omitted_columns"] == 20 - len(retained)
    assert (
        len(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode())
        <= 65536
    )


def test_budget_omits_optional_file_metadata() -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        result=FileJobResult(
            job_id="job-1", file_path="/private/file", media_type="x" * 40000
        )
    )
    wire = _inspect(backend)
    payload = wire["structuredContent"]
    assert wire["isError"] is False
    assert payload["file"] is None
    assert "file" in payload["truncation"]["omitted_sections"]
    assert payload["descriptor"]["url"].endswith("/descriptor")


@pytest.mark.parametrize("tool", ["lyra_get_job_result", "lyra_download_result"])
@pytest.mark.parametrize(
    "reference", ["https://example.test", "lyra://results/a/b", "lyra://results/"]
)
def test_result_reference_validation(tool: str, reference: str) -> None:
    backend = FakeMCPBackend([])
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/",
        json=_tool_call_payload(tool, {"result_ref": reference}),
        headers=_mcp_headers(),
    )
    assert response.json()["result"]["isError"] is True
    assert _tool_payload(response)["error"]["code"] == "invalid_arguments"
    assert backend.observed == []


def test_discovery_preserves_derived_output_declarations() -> None:
    metric = _table_metric("area", "Area measurement")
    metric.output = TableOutput.model_validate(
        {
            "kind": "table",
            "columns": [
                {
                    "name": "area",
                    "type": "number",
                    "unit": "m2",
                    "description": "Area",
                    "derivations": [
                        {
                            "kind": "fraction_of_location_area",
                            "name": "fraction",
                            "description": "Fraction",
                        }
                    ],
                }
            ],
        }
    )
    backend = FakeMCPBackend([metric])
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    for name, arguments in [
        ("lyra_get_metric", {"metric": "area"}),
        ("lyra_search_metrics", {"query": "area"}),
    ]:
        response = client.post(
            "/", json=_tool_call_payload(name, arguments), headers=_mcp_headers()
        )
        assert response.json()["result"]["isError"] is False
        payload = _tool_payload(response)
        validate(payload, TOOL_CONTRACTS_BY_NAME[name].output_schema)
        columns = (
            payload["output"]["columns"]
            if name == "lyra_get_metric"
            else payload["candidates"][0]["relevant_columns"]
        )
        assert columns[0]["derivations"][0]["name"] == "fraction"
    provenance = JobRunProvenance.model_validate(
        {
            "metric": "area",
            "catalog_fingerprint": "catalog",
            "plugin": {"name": "plugin", "version": "1"},
            "created_at": _COMPLETED_AT,
            "output": metric.output,
            "input": {},
        }
    )
    backend.observations["job-1"] = JobObservation(
        provenance=provenance,
        result=TableJobResult(
            job_id="job-1",
            columns=["area", "fraction"],
            index=["row"],
            data=[[10, 0.5]],
        ),
    )
    payload = _inspect(backend)["structuredContent"]
    assert payload["table"]["columns"] == ["area", "fraction"]
    assert "derivations" not in payload["table"]["column_contracts"][0]
    assert provenance.output == metric.output


@pytest.mark.parametrize("parameters", [{}, {"unexpected": 1}])
def test_mcp_parameterless_metric_omits_empty_parameters(
    parameters: dict[str, Any],
) -> None:
    backend = FakeMCPBackend([_file_metric("report", "Write a report.")])
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {"metric": "report", "met_zone_code": "09.01", "parameters": parameters},
        ),
        headers=_mcp_headers(),
    )
    result = response.json()["result"]
    if parameters:
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"] == "invalid_parameters"
        assert backend.payloads == []
    else:
        assert result.get("isError", False) is False
        assert backend.payloads == [
            {"location": {"data_type": "met_zone_code", "value": "09.01"}}
        ]


@pytest.mark.parametrize("parameters", [{}, {"location": "an ordinary parameter"}])
def test_mcp_keeps_declared_parameters_nested(parameters: dict[str, Any]) -> None:
    metric = _table_metric("nested_metric", "Return a table.")
    metric.request_schema = {
        "type": "object",
        "properties": {
            "location": {"type": "object"},
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
            },
        },
        "required": ["location", "parameters"],
        "additionalProperties": False,
    }
    backend = FakeMCPBackend([metric])
    client = _ManagedTestClient(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/",
        json=_tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "nested_metric",
                "met_zone_code": "09.01",
                "parameters": parameters,
            },
        ),
        headers=_mcp_headers(),
    )
    assert response.json()["result"].get("isError", False) is False
    assert backend.payloads == [
        {
            "location": {"data_type": "met_zone_code", "value": "09.01"},
            "parameters": parameters,
        }
    ]
