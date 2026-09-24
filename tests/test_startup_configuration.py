"""Startup-only configuration and offline operator validation contracts."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import FastAPI
from lyra.api import AsyncLyraAdminClient, LyraAdminClient, admin_cli
from lyra.sdk.config import LyraConfig as ConfigDocument
from lyra.sdk.config import PluginRepoConfig, load_config
from pydantic import ValidationError

from lyra_app import plugins, registry
from lyra_app.config import (
    clear_config_cache,
    get_config,
    get_config_path,
    initialize_runtime_config,
)
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.main import catalog_unavailable_response
from lyra_app.mcp.tools import InProcessLyraBackend, ToolCallError
from lyra_app.plugin_runtime import read_snapshot, snapshot_path
from lyra_app.routes import admin, jobs, metrics
from tests.config_helpers import load_test_config
from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR, smoke_plugin_uri

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def startup_config(tmp_path: Path) -> Iterator[None]:
    load_test_config(tmp_path)
    registry.reset_catalog()
    yield
    registry.reset_catalog()
    clear_config_cache()


def test_offline_validation_needs_no_credentials_or_runtime_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "portable.toml"
    shutil.copyfile(Path(__file__).parents[1] / "config.example.toml", path)
    for name in ("LYRA_ADMIN_API_KEY", "LYRA_AGENT_API_KEY", "LYRA_POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    before = path.read_bytes()
    path.chmod(0o444)

    assert admin_cli.main(["--json", "config", "validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True, "schema_version": 2}
    assert path.read_bytes() == before
    assert not (tmp_path / "plugins").exists()


@pytest.mark.parametrize(
    ("contents", "expected"),
    [(None, 1), ("[invalid", 2), ("schema_version = 1", 2)],
)
def test_offline_validation_exit_codes(
    tmp_path: Path,
    contents: str | None,
    expected: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "bad.toml"
    if contents is not None:
        path.write_text(contents)
    assert admin_cli.main(["--json", "config", "validate", str(path)]) == expected
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error"]["kind"] == (
        "operation" if expected == 1 else "usage"
    )


@pytest.mark.parametrize(
    "declaration",
    [
        {"id": "../escape", "source": "owner/repo"},
        {"id": "repo", "source": "owner/repo@main"},
        {"id": "repo", "source": "dir://relative"},
        {"id": "repo", "source": "file://host/path"},
        {"id": "repo", "source": "dir:///path", "ref": "main"},
        {"id": "repo", "source": "owner/repo", "ref": "-option"},
        {"id": "repo", "source": "owner/repo", "routing": {"metric": " "}},
    ],
)
def test_invalid_repo_declarations_are_rejected(declaration: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PluginRepoConfig.model_validate(declaration)


@pytest.mark.parametrize("conflict", ["id", "source", "queue"])
def test_configuration_rejects_repo_conflicts(conflict: str) -> None:
    raw = load_config(get_config_path()).model_dump()
    repos: list[dict[str, object]] = [
        {"id": "first", "source": "owner/first"},
        {"id": "second", "source": "owner/second"},
    ]
    if conflict == "id":
        repos[1]["id"] = "first"
    elif conflict == "source":
        repos[1]["source"] = "https://github.com/owner/first.git"
    else:
        repos[1]["routing"] = {"metric": "unknown"}
    raw["plugins"]["repos"] = repos
    with pytest.raises(ValidationError):
        ConfigDocument.model_validate(raw)


def test_startup_uses_default_and_explicit_routes_and_ignores_disabled_sources() -> (
    None
):
    config = get_config()
    config.plugins.repos = [
        PluginRepoConfig(
            id="smoke",
            source=smoke_plugin_uri(),
            routing={"smoke_table_metric": "batch"},
        ),
        PluginRepoConfig(
            id="disabled",
            source="dir:///does-not-exist",
            enabled=False,
            routing={"absent_metric": "batch"},
        ),
    ]
    original = get_config_path().read_bytes()
    registry.initialize_catalog()
    registry.ensure_catalog_loaded()
    assert registry.get_loaded_metric_queues() == {
        "smoke_table_metric": "batch",
        "smoke_file_metric": "interactive",
        "smoke_cancel_metric": "interactive",
    }
    snapshot = read_snapshot(config)
    assert [source.repo_id for source in snapshot.sources] == ["smoke"]
    routing = admin.list_plugin_routing()
    assert routing.disabled_repos == ["disabled"]
    assert routing.overrides["disabled"] == {"absent_metric": "batch"}
    assert get_config_path().read_bytes() == original


def test_failure_invalidates_previous_snapshot_and_never_retries_on_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = get_config()
    config.plugins.repos = [PluginRepoConfig(id="smoke", source=smoke_plugin_uri())]
    registry.initialize_catalog()
    assert read_snapshot(config).status == "ready"
    config.plugins.repos[0].routing = {"unknown_metric": "batch"}
    registry.initialize_catalog()
    assert not registry.is_catalog_loaded()
    assert json.loads(snapshot_path(config).read_text())["status"] == "failed"
    with pytest.raises(RuntimeError, match="not ready"):
        read_snapshot(config)

    def unexpected_fetch(*_: object) -> None:
        pytest.fail("Catalog reads must never fetch sources")

    monkeypatch.setattr(registry, "prepare_configured_repo", unexpected_fetch)
    with pytest.raises(registry.CatalogUnavailableError):
        registry.get_metric_catalog()
    assert admin.get_catalog().catalog_available is False
    assert admin.get_catalog().catalog_error


def test_workers_reject_missing_malformed_and_mismatched_snapshots() -> None:
    config = get_config()
    with pytest.raises(RuntimeError, match="unavailable"):
        read_snapshot(config)
    registry.initialize_catalog()
    path = snapshot_path(config)
    ready = path.read_text()
    config.api.port += 1
    with pytest.raises(RuntimeError, match="configuration differs"):
        read_snapshot(config)
    config.api.port -= 1
    path.write_text("broken JSON")
    with pytest.raises(RuntimeError, match="unavailable"):
        read_snapshot(config)
    path.write_text(ready)
    assert read_snapshot(config).sources == []


def test_saved_edits_do_not_change_loaded_config_or_registry(tmp_path: Path) -> None:
    config = get_config()
    registry.initialize_catalog()
    path = get_config_path()
    path.write_text("not valid TOML")
    assert get_config() is config
    assert registry.get_metrics_info() == []
    assert admin.get_config_summary().api_port == config.api.port
    assert list(tmp_path.glob("**/plugins.toml")) == []


@pytest.mark.parametrize("operation", ["list", "detail", "submit"])
def test_mcp_reports_unavailable_startup_catalog(operation: str) -> None:
    backend = InProcessLyraBackend(ApplicationDatabaseRuntime(get_config()))
    if operation == "list":
        request = backend.get_metrics()
    elif operation == "detail":
        request = backend.get_metric("smoke_table_metric")
    else:
        request = backend.create_job("smoke_table_metric", {})
    with pytest.raises(ToolCallError) as error:
        asyncio.run(request)
    assert error.value.code == "catalog_unavailable"
    assert error.value.details == {"retryable": True}


def test_public_catalog_and_submissions_are_unavailable_without_startup() -> None:
    app = FastAPI()
    app.state.database = ApplicationDatabaseRuntime(get_config())
    app.add_exception_handler(
        registry.CatalogUnavailableError, catalog_unavailable_response
    )
    app.include_router(metrics.router)
    app.include_router(jobs.router)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ("/metrics", "/metrics/missing"):
                response = await client.get(path)
                assert response.status_code == 503
                assert response.json()["detail"]["code"] == "catalog_unavailable"
            response = await client.post(
                "/jobs",
                headers={"Authorization": "Bearer agent-secret"},
                json={"metric": "missing", "input": {}},
            )
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "catalog_unavailable"

    asyncio.run(exercise())


def test_removed_mutation_routes_and_client_methods() -> None:
    schema_app = FastAPI()
    schema_app.include_router(admin.router)
    operations = {
        (method, path)
        for path, item in schema_app.openapi()["paths"].items()
        for method in item
        if method in {"post", "put", "patch", "delete"}
    }
    assert operations == {("post", "/admin/jobs/{job_id}/cancel")}
    for client in (LyraAdminClient("localhost"), AsyncLyraAdminClient("localhost")):
        assert not hasattr(client.plugin_repos, "create")
        assert not hasattr(client.plugin_repos, "sync")
        assert not hasattr(client.routing, "set")
        assert not hasattr(client.catalog, "refresh")
        assert not hasattr(client.workers, "restart")


def test_install_failure_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = plugins.SyncedPluginRepo(
        entry=plugins.parse_repo_entry(smoke_plugin_uri()),
        path=SMOKE_PLUGIN_DIR,
        changed=False,
    )
    monkeypatch.setattr(plugins, "_check_compatible", lambda _: False)
    with pytest.raises(RuntimeError, match="incompatible"):
        plugins.install_runner_plugins([repo])
    monkeypatch.setattr(plugins, "_check_compatible", lambda _: True)

    def failed_install(_: Path) -> None:
        msg = "installation failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(plugins, "install_plugin", failed_install)
    with pytest.raises(RuntimeError, match="installation failed"):
        plugins.install_runner_plugins([repo])


def test_repository_id_cannot_collide_with_staging_directories() -> None:
    config = get_config()
    config.plugins.repos = [PluginRepoConfig(id="captured", source=smoke_plugin_uri())]
    registry.initialize_catalog()
    assert registry.is_catalog_loaded()
    assert read_snapshot(config).sources[0].repo_id == "captured"


def test_injected_startup_configuration_is_used_by_process_consumers() -> None:
    config = get_config().model_copy(deep=True)
    config.api.port = 7654
    clear_config_cache()
    initialize_runtime_config(config)
    assert get_config() is config
    assert admin.get_config_summary().api_port == 7654
