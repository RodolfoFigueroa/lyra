from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from lyra_app.config import clear_config_cache
from lyra_app.plugins import PluginRepoEntry, SyncedPluginRepo
from lyra_app.registry import CatalogRefreshResult
from lyra_app.routes import admin
from tests.config_helpers import load_test_config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture
def admin_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    load_test_config(tmp_path)
    state_path = tmp_path / "state" / "plugins.toml"
    monkeypatch.setattr(admin, "get_plugin_state_path", lambda: state_path)
    yield state_path
    clear_config_cache()


def _synced_repo(*, changed: bool = True) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw="owner/example-plugin@main",
        clone_url="https://github.com/owner/example-plugin.git",
        owner="owner",
        repo="example-plugin",
        ref="main",
    )
    return SyncedPluginRepo(
        entry=entry,
        path=Path("catalog/owner__example-plugin"),
        changed=changed,
    )


def _assert_http_error(
    status_code: int,
    func: Callable[..., object],
    *args: object,
) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        func(*args)
    assert exc_info.value.status_code == status_code
    return exc_info.value


def test_require_admin_key_reads_configured_secret_file(tmp_path: Path) -> None:
    load_test_config(tmp_path)

    try:
        admin.require_admin_key(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="admin-secret")
        )

        with pytest.raises(HTTPException) as exc_info:
            admin.require_admin_key(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
            )

        assert exc_info.value.status_code == 403
    finally:
        clear_config_cache()


def test_admin_router_requires_bearer_key_for_all_routes() -> None:
    assert admin.router.prefix == "/admin"
    assert any(
        dependency.dependency is admin.require_admin_key
        for dependency in admin.router.dependencies
    )


def test_plugin_repo_endpoints_manage_state(admin_context: Path) -> None:
    created = admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="example",
            source="https://github.com/owner/example-plugin@main",
        )
    )
    listed = admin.list_plugin_repos()
    updated = admin.update_plugin_repo(
        "example",
        admin.UpdatePluginRepoRequest(
            source="owner/example-plugin@v1.2.0",
            enabled=False,
        ),
    )
    deleted = admin.delete_plugin_repo("example")
    missing = _assert_http_error(404, admin.delete_plugin_repo, "example")

    assert created.model_dump() == {
        "id": "example",
        "source": "owner/example-plugin",
        "ref": "main",
        "enabled": True,
    }
    assert "schema_version = 1" in admin_context.read_text(encoding="utf-8")
    assert listed.model_dump()["repos"] == [created.model_dump()]
    assert updated.model_dump() == {
        "id": "example",
        "source": "owner/example-plugin",
        "ref": "v1.2.0",
        "enabled": False,
    }
    assert deleted.model_dump() == {"deleted": True, "repo_id": "example"}
    assert "unknown plugin repo id" in str(missing.detail)


def test_plugin_repo_endpoints_reject_duplicate_ids_and_enabled_sources(
    admin_context: Path,  # noqa: ARG001
) -> None:
    first = admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(id="one", source="owner/example-plugin")
    )

    duplicate_id = _assert_http_error(
        422,
        admin.create_plugin_repo,
        admin.CreatePluginRepoRequest(id="one", source="owner/other-plugin"),
    )
    duplicate_source = _assert_http_error(
        422,
        admin.create_plugin_repo,
        admin.CreatePluginRepoRequest(id="two", source="owner/example-plugin@main"),
    )

    assert first.id == "one"
    assert "provide a unique id" in str(duplicate_id.detail)
    assert "duplicate enabled plugin repo sources" in str(duplicate_source.detail)


def test_pull_plugin_repo_syncs_enabled_repo(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, str]] = []

    def sync_repo(target_dir: Path, raw_entry: str) -> SyncedPluginRepo:
        calls.append((target_dir, raw_entry))
        return _synced_repo(changed=True)

    monkeypatch.setattr(admin, "sync_plugin_repo", sync_repo)
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="example",
            source="owner/example-plugin@main",
        )
    )

    response = admin.pull_plugin_repo("example")

    assert response.model_dump() == {
        "repo_id": "example",
        "changed": True,
        "display_name": "owner/example-plugin",
    }
    assert len(calls) == 1
    assert calls[0][0].name == "catalog"
    assert calls[0][1] == "owner/example-plugin@main"


def test_pull_plugin_repo_returns_contract_errors(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="disabled",
            source="owner/disabled-plugin",
            enabled=False,
        )
    )
    disabled = _assert_http_error(409, admin.pull_plugin_repo, "disabled")
    missing = _assert_http_error(404, admin.pull_plugin_repo, "missing")

    def fail_sync(_target_dir: Path, _raw_entry: str) -> SyncedPluginRepo:
        raise subprocess.CalledProcessError(
            1,
            ["git", "fetch"],
            stderr="git failed",
        )

    monkeypatch.setattr(admin, "sync_plugin_repo", fail_sync)
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(id="broken", source="owner/broken-plugin")
    )
    failed = _assert_http_error(502, admin.pull_plugin_repo, "broken")

    assert "disabled" in str(disabled.detail)
    assert "missing" in str(missing.detail)
    assert failed.detail == "git failed"


def test_refresh_plugin_catalog_uses_state_refresh_and_restarts_workers(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = CatalogRefreshResult(
        updated_plugins=["owner/example-plugin"],
        previous_catalog_fingerprint="old",
        catalog_fingerprint="new",
        catalog_changed=True,
        assigned_metric_queues=["walkability_score"],
    )
    restarted: list[float] = []

    def restart_workers(*, timeout: float) -> None:
        restarted.append(timeout)

    monkeypatch.setattr(admin, "refresh_catalog_from_state", lambda _store: result)
    monkeypatch.setattr(admin, "graceful_worker_restart", restart_workers)

    response = admin.refresh_plugin_catalog(timeout=12.5)

    assert response.model_dump() == {
        "updated_plugins": ["owner/example-plugin"],
        "catalog_changed": True,
        "previous_catalog_fingerprint": "old",
        "catalog_fingerprint": "new",
        "assigned_metric_queues": ["walkability_score"],
        "message": (
            "Updated 1 plugin repo(s): owner/example-plugin. Catalog changed "
            "(new). Workers are restarting."
        ),
    }
    assert restarted == [12.5]


def test_refresh_plugin_catalog_reports_git_failures(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restarted: list[float] = []

    def fail_refresh(_store: object) -> CatalogRefreshResult:
        raise subprocess.CalledProcessError(
            1,
            ["git", "fetch"],
            stderr="catalog sync failed",
        )

    monkeypatch.setattr(admin, "refresh_catalog_from_state", fail_refresh)
    monkeypatch.setattr(
        admin,
        "graceful_worker_restart",
        lambda *, timeout: restarted.append(timeout),
    )

    failed = _assert_http_error(502, admin.refresh_plugin_catalog)

    assert failed.detail == "catalog sync failed"
    assert restarted == []


def test_plugin_routing_endpoints_manage_state(
    admin_context: Path,  # noqa: ARG001
) -> None:
    initial = admin.list_plugin_routing()
    created = admin.set_plugin_routing(
        "walkability_score",
        admin.SetMetricQueueRequest(queue="batch"),
    )
    invalid = _assert_http_error(
        422,
        admin.set_plugin_routing,
        "walkability_score",
        admin.SetMetricQueueRequest(queue="not-allowed"),
    )
    listed = admin.list_plugin_routing()
    deleted = admin.delete_plugin_routing("walkability_score")
    deleted_again = admin.delete_plugin_routing("walkability_score")

    assert initial.metric_queues == {}
    assert initial.default_queue == "interactive"
    assert "batch" in initial.allowed_queues
    assert created.model_dump() == {
        "metric_name": "walkability_score",
        "queue": "batch",
    }
    assert "plugins.allowed_queues" in str(invalid.detail)
    assert listed.metric_queues == {"walkability_score": "batch"}
    assert deleted.model_dump() == {
        "deleted": True,
        "metric_name": "walkability_score",
    }
    assert deleted_again.model_dump() == {
        "deleted": False,
        "metric_name": "walkability_score",
    }


def test_admin_openapi_exposes_new_paths_without_old_update_route() -> None:
    app = FastAPI()
    app.include_router(admin.router)

    paths = set(app.openapi()["paths"])

    assert "/admin/plugin-repos" in paths
    assert "/admin/plugin-repos/{repo_id}" in paths
    assert "/admin/plugin-repos/{repo_id}/pull" in paths
    assert "/admin/plugin-catalog/refresh" in paths
    assert "/admin/plugin-routing" in paths
    assert "/admin/plugin-routing/{metric_name}" in paths
    assert "/update-plugins" not in paths
