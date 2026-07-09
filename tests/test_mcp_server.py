from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient
from lyra.mcp import SERVER_INSTRUCTIONS, create_mcp_app
from lyra.sdk.models import (
    JobCreateResponse,
    JobLifecycleStatus,
    JobLinks,
    JobStatusInfo,
    TableJobResult,
    build_result_descriptor,
)
from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfoV3
from lyra.sdk.models.plugin_v3 import (
    FileOutputV3,
    SpatialInputKindV3,
    TableOutputColumnV3,
    TableOutputV3,
)

from lyra_app import main
from tests.config_helpers import load_test_config

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from lyra_app.config import LyraConfig


def _initialize_payload() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }


def _mcp_headers(bearer: str | None = None) -> dict[str, str]:
    token = "mcp-secret" if bearer is None else bearer
    return {"Authorization": f"Bearer {token}"}


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


def _tool_payload(response: Any) -> dict[str, Any]:
    assert response.status_code == 200
    result = response.json()["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"]


def _app_with_mcp(
    config: LyraConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(
        main, "bootstrap_runtime", lambda runtime_config: runtime_config
    )

    from lyra_app import registry  # noqa: PLC0415

    monkeypatch.setattr(registry, "ensure_catalog_loaded", lambda: None)
    return TestClient(main.create_app(config))


class FakeMCPBackend:
    def __init__(self, metrics: list[MetricInfoV3]) -> None:
        self.catalog = MetricCatalogResponse(
            catalog_fingerprint="catalog-1",
            metrics=metrics,
        )
        self.jobs: dict[str, JobStatusInfo] = {}
        self.descriptors: dict[str, Any] = {}
        self.payloads: list[dict[str, Any]] = []
        self.job_status_sequence: list[JobLifecycleStatus] = ["succeeded"]

    async def get_metrics(self) -> MetricCatalogResponse:
        return self.catalog

    async def get_metric(self, metric: str) -> MetricInfoV3 | None:
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
    ) -> JobCreateResponse:
        if payload.get("value") == "invalid":
            code = "invalid_parameters"
            message = "Invalid metric parameters."
            details = [{"loc": ["value"], "msg": "Expected integer.", "type": "type"}]
            raise self._tool_error(
                code,
                message,
                details,
            )

        job_id = f"job-{len(self.payloads) + 1}"
        self.payloads.append(payload)
        self.jobs[job_id] = self._job_status(
            job_id, self.job_status_sequence[0], metric
        )
        self.descriptors[job_id] = build_result_descriptor(
            TableJobResult(
                job_id=job_id,
                index=["area-1"],
                columns=["value"],
                data=[[payload.get("value", 1)]],
            )
        )
        return JobCreateResponse(
            job_id=job_id,
            metric=metric,
            status="queued",
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

    async def get_result_descriptor(self, job_id: str) -> Any | None:
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
    def _tool_error(code: str, message: str, details: Any) -> Exception:
        from lyra.mcp.server import ToolCallError  # noqa: PLC0415

        return ToolCallError(code, message, details)


def _table_metric(
    name: str,
    description: str,
    *,
    spatial_inputs: dict[str, SpatialInputKindV3] | None = None,
    value_type: str = "integer",
) -> MetricInfoV3:
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
    return MetricInfoV3(
        name=name,
        description=description,
        request_schema={
            "type": "object",
            "properties": properties,
            "required": [*spatial, "value"],
            "additionalProperties": False,
        },
        spatial_inputs=spatial,
        output=TableOutputV3(
            kind="table",
            columns=[
                TableOutputColumnV3(
                    name="value",
                    type="integer",
                    unit="count",
                    description="Submitted value.",
                )
            ],
        ),
    )


def _file_metric(name: str, description: str) -> MetricInfoV3:
    return MetricInfoV3(
        name=name,
        description=description,
        request_schema={
            "type": "object",
            "properties": {"location": {"type": "object"}},
            "required": ["location"],
            "additionalProperties": False,
        },
        spatial_inputs={"location": "location"},
        output=FileOutputV3(kind="file", media_type="text/plain", extensions=[".txt"]),
    )


def test_mcp_package_initializes_with_bearer_auth() -> None:
    app = create_mcp_app(api_key="mcp-secret")
    client = TestClient(app)

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
    assert result["capabilities"] == {"tools": {}}
    assert result["instructions"] == SERVER_INSTRUCTIONS
    assert "metropolitan zone codes" in result["instructions"]
    assert "lyra://results/{job_id}" in result["instructions"]
    assert "poll the result tools" in result["instructions"]


def test_mcp_package_exposes_discovery_health_and_static_tools() -> None:
    client = TestClient(create_mcp_app(api_key="mcp-secret"))

    discovery = client.get("/", headers=_mcp_headers())
    health = client.get("/health", headers=_mcp_headers())
    tools = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=_mcp_headers(),
    )

    assert discovery.status_code == 200
    assert discovery.json()["transport"] == "streamable-http"
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert tools.status_code == 200
    tool_names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert tool_names == {
        "lyra_search_metrics",
        "lyra_get_metric",
        "lyra_run_metric",
    }
    run_tool = next(
        tool
        for tool in tools.json()["result"]["tools"]
        if tool["name"] == "lyra_run_metric"
    )
    assert "do not rerun" in run_tool["description"]
    assert "lyra_get_job_result" in run_tool["description"]
    assert "admin" not in tools.text.lower()


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
    client = TestClient(create_mcp_app(api_key="mcp-secret", backend=backend))

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


def test_mcp_get_metric_returns_public_contract() -> None:
    metric = _table_metric("smoke_table_metric", "Return a table.")
    client = TestClient(
        create_mcp_app(api_key="mcp-secret", backend=FakeMCPBackend([metric]))
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
    client = TestClient(create_mcp_app(api_key="mcp-secret", backend=backend))

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
    client = TestClient(create_mcp_app(api_key="mcp-secret", backend=backend))

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
    client = TestClient(create_mcp_app(api_key="mcp-secret", backend=backend))

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
    }


def test_mcp_run_metric_surfaces_unknown_metric_as_tool_error() -> None:
    client = TestClient(
        create_mcp_app(api_key="mcp-secret", backend=FakeMCPBackend([]))
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
    client = TestClient(create_mcp_app(api_key="mcp-secret", backend=backend))

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
    client = TestClient(create_mcp_app(api_key="mcp-secret", backend=backend))

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
    monkeypatch.setenv("LYRA_MCP_API_KEY", "mcp-secret")
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
    monkeypatch.setenv("LYRA_MCP_API_KEY", "mcp-secret")
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
