from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from redis.exceptions import RedisError

from lyra_app import job_store
from lyra_app.config import clear_config_cache
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from lyra_app.registry import CatalogRefreshResult, reset_catalog
from lyra_app.routes import admin
from tests.config_helpers import load_test_config
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    directory_uri,
    smoke_plugin_uri,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations.append((key, ex))

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    def xadd(self, key: str, fields: dict[str, str]) -> str:
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, fields))
        return stream_id

    def xrange(
        self,
        key: str,
        *,
        min: str,  # noqa: A002
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        records = self.streams.get(key, [])
        if min.startswith("("):
            after_id = min[1:]
            records = [record for record in records if record[0] > after_id]
        return records if count is None else records[:count]

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        members = sorted(
            self.sorted_sets.get(key, {}),
            key=lambda member: self.sorted_sets[key][member],
            reverse=True,
        )
        return members[start : stop + 1]

    def zrem(self, key: str, *members: str) -> None:
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member in members:
            sorted_set.pop(member, None)

    def zremrangebyscore(self, key: str, min: str | float, max: float) -> None:  # noqa: A002
        lower = float("-inf") if min == "-inf" else float(min)
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member, score in list(sorted_set.items()):
            if lower <= score <= max:
                sorted_set.pop(member, None)


class FailingRedisSync(FakeRedisSync):
    def zremrangebyscore(self, *_args: object, **_kwargs: object) -> None:
        raise RedisError


@pytest.fixture
def admin_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    reset_catalog()
    load_test_config(tmp_path)
    state_path = tmp_path / "state" / "plugins.toml"
    monkeypatch.setattr(admin, "get_plugin_state_path", lambda: state_path)
    yield state_path
    reset_catalog()
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


def _catalog_refresh_result(
    *,
    updated_plugins: list[str] | None = None,
    catalog_changed: bool = False,
    previous_catalog_fingerprint: str = "same",
    catalog_fingerprint: str = "same",
    assigned_metric_queues: list[str] | None = None,
    removed_metric_queues: list[str] | None = None,
) -> CatalogRefreshResult:
    return CatalogRefreshResult(
        updated_plugins=updated_plugins or [],
        previous_catalog_fingerprint=previous_catalog_fingerprint,
        catalog_fingerprint=catalog_fingerprint,
        catalog_changed=catalog_changed,
        assigned_metric_queues=assigned_metric_queues or [],
        removed_metric_queues=removed_metric_queues or [],
    )


def _catalog_refresh_status(
    *,
    refreshed: bool = True,
    error: str | None = None,
    catalog_changed: bool | None = False,
    previous_catalog_fingerprint: str | None = "same",
    catalog_fingerprint: str | None = "same",
    assigned_metric_queues: list[str] | None = None,
    removed_metric_queues: list[str] | None = None,
    workers_restart_recommended: bool = False,
) -> dict[str, object]:
    return {
        "refreshed": refreshed,
        "error": error,
        "catalog_changed": catalog_changed,
        "previous_catalog_fingerprint": previous_catalog_fingerprint,
        "catalog_fingerprint": catalog_fingerprint,
        "assigned_metric_queues": assigned_metric_queues or [],
        "removed_metric_queues": removed_metric_queues or [],
        "workers_restart_recommended": workers_restart_recommended,
    }


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


def test_plugin_repo_endpoints_manage_state(
    admin_context: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        admin,
        "refresh_catalog_from_state",
        lambda _store: _catalog_refresh_result(),
    )
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
        "repo": {
            "id": "example",
            "source": "owner/example-plugin",
            "ref": "main",
            "enabled": True,
        },
        "catalog_refresh": _catalog_refresh_status(),
    }
    assert "schema_version = 1" in admin_context.read_text(encoding="utf-8")
    assert listed.model_dump()["repos"] == [created.repo.model_dump()]
    assert updated.model_dump() == {
        "repo": {
            "id": "example",
            "source": "owner/example-plugin",
            "ref": "v1.2.0",
            "enabled": False,
        },
        "catalog_refresh": _catalog_refresh_status(),
    }
    assert deleted.model_dump() == {
        "deleted": True,
        "repo_id": "example",
        "removed_metric_queues": [],
        "catalog_refresh": _catalog_refresh_status(),
    }
    assert "unknown plugin repo id" in str(missing.detail)


def test_delete_plugin_repo_removes_owned_metric_routes(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = admin._state_store()  # noqa: SLF001
    store.add_repo("owner/example-plugin", repo_id="example")
    store.add_repo("owner/other-plugin", repo_id="other")
    store.set_metric_queue("walkability_score", "batch", repo_id="example")
    store.set_metric_queue("other_score", "interactive", repo_id="other")
    result = _catalog_refresh_result(
        previous_catalog_fingerprint="old",
        catalog_fingerprint="new",
        catalog_changed=True,
    )
    monkeypatch.setattr(admin, "refresh_catalog_from_state", lambda _store: result)

    deleted = admin.delete_plugin_repo("example")

    assert deleted.model_dump() == {
        "deleted": True,
        "repo_id": "example",
        "removed_metric_queues": ["walkability_score"],
        "catalog_refresh": _catalog_refresh_status(
            previous_catalog_fingerprint="old",
            catalog_fingerprint="new",
            catalog_changed=True,
            workers_restart_recommended=True,
        ),
    }
    assert admin.list_plugin_routing().metric_queues == {"other_score": "interactive"}


def test_delete_plugin_repo_clears_loaded_catalog_when_refresh_fails(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = admin._state_store()  # noqa: SLF001
    store.add_repo("owner/example-plugin", repo_id="example")
    store.set_metric_queue("walkability_score", "batch", repo_id="example")
    reset_calls: list[None] = []

    def fail_refresh(_store: object) -> CatalogRefreshResult:
        msg = "catalog refresh failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(admin, "refresh_catalog_from_state", fail_refresh)
    monkeypatch.setattr(admin, "reset_catalog", lambda: reset_calls.append(None))

    deleted = admin.delete_plugin_repo("example")

    assert deleted.model_dump() == {
        "deleted": True,
        "repo_id": "example",
        "removed_metric_queues": ["walkability_score"],
        "catalog_refresh": _catalog_refresh_status(
            refreshed=False,
            error="catalog refresh failed",
            catalog_changed=None,
            previous_catalog_fingerprint=None,
            catalog_fingerprint=None,
        ),
    }
    assert reset_calls == [None]
    assert admin.list_plugin_routing().metric_queues == {}


def test_create_plugin_repo_refreshes_catalog_and_exposes_metrics(
    admin_context: Path,  # noqa: ARG001
) -> None:
    created = admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(id="smoke", source=smoke_plugin_uri())
    )

    catalog = admin.get_catalog()
    routing = admin.list_plugin_routing()

    assert created.repo.id == "smoke"
    assert created.catalog_refresh.refreshed is True
    assert sorted(created.catalog_refresh.assigned_metric_queues) == sorted(
        SMOKE_METRIC_QUEUES
    )
    assert catalog.metric_names == sorted(SMOKE_METRIC_QUEUES)
    assert routing.metric_queues == SMOKE_METRIC_QUEUES


def test_update_plugin_repo_refreshes_catalog_and_prunes_disabled_metrics(
    admin_context: Path,  # noqa: ARG001
) -> None:
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(id="smoke", source=smoke_plugin_uri())
    )

    updated = admin.update_plugin_repo(
        "smoke",
        admin.UpdatePluginRepoRequest(enabled=False),
    )

    assert updated.repo.enabled is False
    assert updated.catalog_refresh.refreshed is True
    assert sorted(updated.catalog_refresh.removed_metric_queues) == sorted(
        SMOKE_METRIC_QUEUES
    )
    assert admin.get_catalog().metric_names == []
    assert admin.list_plugin_routing().metric_queues == {}


def test_create_plugin_repo_reports_refresh_failure_without_rollback(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_calls: list[None] = []

    def fail_refresh(_store: object) -> CatalogRefreshResult:
        msg = "catalog refresh failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(admin, "refresh_catalog_from_state", fail_refresh)
    monkeypatch.setattr(admin, "reset_catalog", lambda: reset_calls.append(None))

    created = admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(id="example", source="owner/example-plugin")
    )

    assert created.model_dump() == {
        "repo": {
            "id": "example",
            "source": "owner/example-plugin",
            "ref": None,
            "enabled": True,
        },
        "catalog_refresh": _catalog_refresh_status(
            refreshed=False,
            error="catalog refresh failed",
            catalog_changed=None,
            previous_catalog_fingerprint=None,
            catalog_fingerprint=None,
        ),
    }
    assert admin.list_plugin_repos().repos[0].id == "example"
    assert reset_calls == [None]


def test_update_plugin_repo_reports_refresh_failure_without_rollback(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = admin._state_store()  # noqa: SLF001
    store.add_repo("owner/example-plugin", repo_id="example")
    reset_calls: list[None] = []

    def fail_refresh(_store: object) -> CatalogRefreshResult:
        msg = "catalog refresh failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(admin, "refresh_catalog_from_state", fail_refresh)
    monkeypatch.setattr(admin, "reset_catalog", lambda: reset_calls.append(None))

    updated = admin.update_plugin_repo(
        "example",
        admin.UpdatePluginRepoRequest(enabled=False),
    )

    assert updated.repo.enabled is False
    assert updated.catalog_refresh.refreshed is False
    assert updated.catalog_refresh.error == "catalog refresh failed"
    assert admin.list_plugin_repos().repos[0].enabled is False
    assert reset_calls == [None]


def test_plugin_repo_endpoints_manage_directory_source(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        admin,
        "refresh_catalog_from_state",
        lambda _store: _catalog_refresh_result(),
    )
    source = tmp_path / "mock-plugin"
    source.mkdir()
    manifest_path = source / MANIFEST_FILENAME
    manifest_path.write_text("{}", encoding="utf-8")
    normalized_source = directory_uri(source)

    created = admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="mock",
            source=f"dir://localhost{source}",
        )
    )
    listed = admin.list_plugin_repos()
    synced = admin.sync_plugin_repo("mock")
    synced_again = admin.sync_plugin_repo("mock")
    manifest_path.write_text('{"changed": true}', encoding="utf-8")
    synced_edited = admin.sync_plugin_repo("mock")

    assert created.model_dump() == {
        "repo": {
            "id": "mock",
            "source": normalized_source,
            "ref": None,
            "enabled": True,
        },
        "catalog_refresh": _catalog_refresh_status(),
    }
    assert listed.model_dump()["repos"] == [created.repo.model_dump()]
    assert synced.model_dump() == {
        "repo_id": "mock",
        "changed": True,
        "display_name": f"dir:{source.resolve()}",
        "catalog_refresh": _catalog_refresh_status(),
    }
    assert synced_again.model_dump() == {
        "repo_id": "mock",
        "changed": False,
        "display_name": f"dir:{source.resolve()}",
        "catalog_refresh": _catalog_refresh_status(),
    }
    assert synced_edited.model_dump() == {
        "repo_id": "mock",
        "changed": True,
        "display_name": f"dir:{source.resolve()}",
        "catalog_refresh": _catalog_refresh_status(),
    }
    copied_manifests = list(
        (tmp_path / "plugins" / "catalog").glob(
            f"dir__mock-plugin__*/{MANIFEST_FILENAME}"
        )
    )
    assert len(copied_manifests) == 1


def test_plugin_repo_update_can_switch_to_directory_source(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        admin,
        "refresh_catalog_from_state",
        lambda _store: _catalog_refresh_result(),
    )
    source = tmp_path / "mock-plugin"
    source.mkdir()
    (source / MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="mock",
            source="owner/example-plugin@main",
        )
    )

    updated = admin.update_plugin_repo(
        "mock",
        admin.UpdatePluginRepoRequest(source=f"dir://localhost{source}"),
    )

    assert updated.model_dump() == {
        "repo": {
            "id": "mock",
            "source": directory_uri(source),
            "ref": None,
            "enabled": True,
        },
        "catalog_refresh": _catalog_refresh_status(),
    }


def test_plugin_repo_endpoints_reject_duplicate_ids_and_enabled_sources(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        admin,
        "refresh_catalog_from_state",
        lambda _store: _catalog_refresh_result(),
    )
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

    assert first.repo.id == "one"
    assert "provide a unique id" in str(duplicate_id.detail)
    assert "duplicate enabled plugin repo sources" in str(duplicate_source.detail)


def test_sync_plugin_repo_syncs_enabled_repo(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, str]] = []
    refresh_calls: list[object] = []

    def sync_source(target_dir: Path, raw_entry: str) -> SyncedPluginRepo:
        calls.append((target_dir, raw_entry))
        return _synced_repo(changed=True)

    def refresh_catalog(store: object) -> CatalogRefreshResult:
        refresh_calls.append(store)
        return _catalog_refresh_result()

    monkeypatch.setattr(admin, "sync_plugin_source", sync_source)
    monkeypatch.setattr(admin, "refresh_catalog_from_state", refresh_catalog)
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="example",
            source="owner/example-plugin@main",
        )
    )

    response = admin.sync_plugin_repo("example")

    assert response.model_dump() == {
        "repo_id": "example",
        "changed": True,
        "display_name": "owner/example-plugin",
        "catalog_refresh": _catalog_refresh_status(),
    }
    assert len(calls) == 1
    assert calls[0][0].name == "catalog"
    assert calls[0][1] == "owner/example-plugin@main"
    assert len(refresh_calls) == 2


def test_sync_plugin_repo_reports_refresh_failure_after_successful_sync(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = admin._state_store()  # noqa: SLF001
    store.add_repo("owner/example-plugin@main", repo_id="example")
    reset_calls: list[None] = []

    def fail_refresh(_store: object) -> CatalogRefreshResult:
        msg = "catalog refresh failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        admin,
        "sync_plugin_source",
        lambda _target_dir, _raw_entry: _synced_repo(changed=True),
    )
    monkeypatch.setattr(admin, "refresh_catalog_from_state", fail_refresh)
    monkeypatch.setattr(admin, "reset_catalog", lambda: reset_calls.append(None))

    response = admin.sync_plugin_repo("example")

    assert response.changed is True
    assert response.catalog_refresh.refreshed is False
    assert response.catalog_refresh.error == "catalog refresh failed"
    assert reset_calls == [None]


def test_sync_plugin_repo_returns_contract_errors(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        admin,
        "refresh_catalog_from_state",
        lambda _store: _catalog_refresh_result(),
    )
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="disabled",
            source="owner/disabled-plugin",
            enabled=False,
        )
    )
    disabled = _assert_http_error(409, admin.sync_plugin_repo, "disabled")
    missing = _assert_http_error(404, admin.sync_plugin_repo, "missing")

    def fail_sync(_target_dir: Path, _raw_entry: str) -> SyncedPluginRepo:
        raise subprocess.CalledProcessError(
            1,
            ["git", "fetch"],
            stderr="git failed",
        )

    monkeypatch.setattr(admin, "sync_plugin_source", fail_sync)
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(id="broken", source="owner/broken-plugin")
    )
    failed = _assert_http_error(502, admin.sync_plugin_repo, "broken")

    assert "disabled" in str(disabled.detail)
    assert "missing" in str(missing.detail)
    assert failed.detail == "git failed"


def test_sync_plugin_repo_reports_directory_sync_failures(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        admin,
        "refresh_catalog_from_state",
        lambda _store: _catalog_refresh_result(),
    )
    missing_source = tmp_path / "missing-plugin"
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="missing-dir",
            source=f"dir://{missing_source}",
        )
    )

    failed = _assert_http_error(502, admin.sync_plugin_repo, "missing-dir")

    assert "Directory plugin source does not exist" in str(failed.detail)


def test_refresh_plugin_catalog_uses_state_refresh_without_restarting_workers(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = CatalogRefreshResult(
        updated_plugins=["owner/example-plugin"],
        previous_catalog_fingerprint="old",
        catalog_fingerprint="new",
        catalog_changed=True,
        assigned_metric_queues=["walkability_score"],
        removed_metric_queues=[],
    )
    restarted: list[float] = []

    def restart_workers(*, timeout: float) -> None:
        restarted.append(timeout)

    monkeypatch.setattr(admin, "refresh_catalog_from_state", lambda _store: result)
    monkeypatch.setattr(admin, "graceful_worker_restart", restart_workers)

    response = admin.refresh_plugin_catalog()

    assert response.model_dump() == {
        "updated_plugins": ["owner/example-plugin"],
        "catalog_changed": True,
        "previous_catalog_fingerprint": "old",
        "catalog_fingerprint": "new",
        "assigned_metric_queues": ["walkability_score"],
        "removed_metric_queues": [],
        "workers_restarted": False,
        "workers_restart_recommended": True,
        "message": (
            "Updated 1 plugin repo(s): owner/example-plugin. Catalog changed "
            "(new). Workers were not restarted."
        ),
    }
    assert restarted == []


def test_refresh_plugin_catalog_does_not_recommend_restart_when_unchanged(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = CatalogRefreshResult(
        updated_plugins=[],
        previous_catalog_fingerprint="same",
        catalog_fingerprint="same",
        catalog_changed=False,
        assigned_metric_queues=[],
        removed_metric_queues=[],
    )
    monkeypatch.setattr(admin, "refresh_catalog_from_state", lambda _store: result)

    response = admin.refresh_plugin_catalog()

    assert response.workers_restarted is False
    assert response.workers_restart_recommended is False
    assert response.message == (
        "No plugin repo changes detected. Catalog unchanged (same). "
        "Workers were not restarted."
    )


def test_restart_workers_calls_worker_control_with_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restarted: list[float] = []

    def restart_workers(*, timeout: float) -> None:
        restarted.append(timeout)

    monkeypatch.setattr(admin, "graceful_worker_restart", restart_workers)

    response = admin.restart_workers(timeout=12.5)

    assert response.model_dump() == {
        "requested": True,
        "timeout": 12.5,
        "message": "Worker restart requested.",
    }
    assert restarted == [12.5]


def test_admin_jobs_list_returns_empty_response(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin.job_store, "redis_client_sync", FakeRedisSync())

    response = admin.list_jobs()

    assert response.model_dump() == {"jobs": []}


def test_admin_jobs_list_filters_by_status_and_metric(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    monkeypatch.setattr(admin.job_store, "redis_client_sync", redis)
    job_store.set_job_status("job-1", "queued", metric="heavy_metric", client=redis)
    job_store.set_job_status("job-2", "started", metric="heavy_metric", client=redis)
    job_store.set_job_status("job-3", "started", metric="light_metric", client=redis)

    response = admin.list_jobs(limit=10, status="started", metric="heavy_metric")

    assert [job.job_id for job in response.jobs] == ["job-2"]


def test_admin_jobs_list_returns_503_when_redis_is_unavailable(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin.job_store, "redis_client_sync", FailingRedisSync())

    failed = _assert_http_error(503, admin.list_jobs)

    assert "Cannot connect to Redis" in str(failed.detail)


def test_admin_jobs_limit_validation_is_documented_in_openapi() -> None:
    app = FastAPI()
    app.include_router(admin.router)
    operation = app.openapi()["paths"]["/admin/jobs"]["get"]

    limit_parameter = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "limit"
    )

    assert limit_parameter["schema"]["minimum"] == 1
    assert limit_parameter["schema"]["maximum"] == 100


def test_admin_cancel_job_marks_active_job_and_revokes_task(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    revoked: list[str] = []
    monkeypatch.setattr(admin.job_store, "redis_client_sync", redis)
    monkeypatch.setattr(admin, "revoke_job", revoked.append)
    job_store.set_job_status("job-1", "progress", metric="heavy_metric", client=redis)

    response = admin.cancel_job("job-1")

    assert response.model_dump() == {
        "job_id": "job-1",
        "status": "cancelled",
        "cancellation_requested": True,
        "revoke_requested": True,
    }
    stored_snapshot = job_store.get_job_status("job-1", client=redis)
    assert stored_snapshot is not None
    assert stored_snapshot.status == "cancelled"
    assert [
        event.event.event for event in job_store.read_job_events("job-1", client=redis)
    ] == [
        "progress",
        "cancelled",
    ]
    assert revoked == ["job-1"]


def test_admin_cancel_job_rejects_terminal_job(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    revoked: list[str] = []
    monkeypatch.setattr(admin.job_store, "redis_client_sync", redis)
    monkeypatch.setattr(admin, "revoke_job", revoked.append)
    job_store.set_job_status("job-1", "succeeded", metric="heavy_metric", client=redis)

    failed = _assert_http_error(409, admin.cancel_job, "job-1")

    assert "already terminal" in str(failed.detail)
    stored_snapshot = job_store.get_job_status("job-1", client=redis)
    assert stored_snapshot is not None
    assert stored_snapshot.status == "succeeded"
    assert revoked == []


def test_admin_cancel_job_returns_404_for_unknown_job(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin.job_store, "redis_client_sync", FakeRedisSync())

    failed = _assert_http_error(404, admin.cancel_job, "missing")

    assert "Job expired or not found" in str(failed.detail)


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


def test_refresh_plugin_catalog_reports_directory_sync_failures(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    restarted: list[float] = []
    missing_source = tmp_path / "missing-plugin"
    admin.create_plugin_repo(
        admin.CreatePluginRepoRequest(
            id="missing-dir",
            source=f"dir://{missing_source}",
        )
    )
    monkeypatch.setattr(
        admin,
        "graceful_worker_restart",
        lambda *, timeout: restarted.append(timeout),
    )

    failed = _assert_http_error(502, admin.refresh_plugin_catalog)

    assert "Directory plugin source does not exist" in str(failed.detail)
    assert restarted == []


def test_plugin_routing_endpoints_manage_state(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = admin._state_store()  # noqa: SLF001
    store.add_repo("owner/example-plugin", repo_id="example")
    monkeypatch.setattr(
        admin,
        "get_metric_entry",
        lambda metric_name: (
            SimpleNamespace(repo_id="example")
            if metric_name == "walkability_score"
            else None
        ),
    )

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


def test_plugin_routing_rejects_unknown_metric(
    admin_context: Path,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin, "get_metric_entry", lambda _metric_name: None)

    missing = _assert_http_error(
        404,
        admin.set_plugin_routing,
        "missing_metric",
        admin.SetMetricQueueRequest(queue="batch"),
    )

    assert "Metric 'missing_metric' not found" in str(missing.detail)


def test_admin_openapi_exposes_new_paths_without_old_update_route() -> None:
    app = FastAPI()
    app.include_router(admin.router)

    paths = set(app.openapi()["paths"])

    assert "/admin/plugin-repos" in paths
    assert "/admin/plugin-repos/{repo_id}" in paths
    assert "/admin/plugin-repos/{repo_id}/sync" in paths
    assert "/admin/plugin-repos/{repo_id}/pull" not in paths
    assert "/admin/plugin-catalog/refresh" in paths
    assert "/admin/workers/restart" in paths
    assert "/admin/status" in paths
    assert "/admin/config-summary" in paths
    assert "/admin/catalog" in paths
    assert "/admin/workers" in paths
    assert "/admin/workers/{worker_name}" in paths
    assert "/admin/queues" in paths
    assert "/admin/jobs" in paths
    assert "/admin/jobs/{job_id}/cancel" in paths
    assert "/admin/plugin-routing" in paths
    assert "/admin/plugin-routing/{metric_name}" in paths
    assert "/update-plugins" not in paths
