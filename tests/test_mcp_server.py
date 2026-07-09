from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from lyra.mcp import SERVER_INSTRUCTIONS, create_mcp_app

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


def test_mcp_package_exposes_discovery_health_and_no_tools() -> None:
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
    assert tools.json()["result"]["tools"] == []
    assert "admin" not in tools.text.lower()


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
