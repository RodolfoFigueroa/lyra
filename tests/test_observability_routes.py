import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from redis.exceptions import RedisError

from lyra_app.config import clear_config_cache, get_config
from lyra_app.registry import refresh_catalog, reset_catalog
from lyra_app.routes import admin, health
from lyra_app.worker_control import WorkerInspectSnapshot
from tests.config_helpers import load_test_config, plugin_state_path, plugin_state_store
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
    smoke_plugin_uri,
)


class FakeRedisAsync:
    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail

    async def ping(self) -> bool:
        if self.fail:
            raise RedisError
        return self.available


class FakeRedisSync:
    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail

    def ping(self) -> bool:
        if self.fail:
            raise RedisError
        return self.available


@pytest.fixture(autouse=True)
def _reset_catalog() -> Iterator[None]:
    reset_catalog()
    yield
    reset_catalog()
    clear_config_cache()


def _configure_admin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    metric_queues: dict[str, str] | None = None,
    repos: list[str] | None = None,
) -> None:
    load_test_config(tmp_path, metric_queues=metric_queues, repos=repos)
    monkeypatch.setattr(
        admin,
        "get_plugin_state_path",
        lambda: plugin_state_path(tmp_path),
    )


def test_health_reports_healthy_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "redis_client", FakeRedisAsync())

    response = asyncio.run(health.health_check())

    assert response.status == "ok"
    assert response.redis.status == "ok"


def test_health_reports_unavailable_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "redis_client", FakeRedisAsync(fail=True))

    response = asyncio.run(health.health_check())

    assert response.status == "degraded"
    assert response.redis.status == "unavailable"


def test_admin_status_excludes_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)
    monkeypatch.setattr(admin.job_store, "redis_client_sync", FakeRedisSync())

    response = admin.get_status()
    payload = response.model_dump_json()

    assert response.redis.status == "ok"
    assert response.metric_count == 0
    assert "admin-secret" not in payload
    assert "postgres-secret" not in payload
    assert "client_email" not in payload


def test_config_summary_excludes_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)

    response = admin.get_config_summary()
    payload = response.model_dump_json()

    assert response.default_queue == "interactive"
    assert response.plugin_catalog_dir == str(tmp_path / "plugins" / "catalog")
    assert "admin-secret" not in payload
    assert "postgres-secret" not in payload
    assert "service-account" not in payload
    assert "client_email" not in payload


def test_catalog_metadata_reports_empty_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)

    response = admin.get_catalog()

    assert response.metric_count == 0
    assert response.metric_names == []
    assert response.plugin_sources == []
    assert response.metric_queues == {}


def test_catalog_metadata_reports_smoke_directory_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(
        tmp_path,
        monkeypatch,
        metric_queues=SMOKE_METRIC_QUEUES,
        repos=[smoke_plugin_uri()],
    )
    refresh_catalog(plugin_state_store(tmp_path, get_config()))

    response = admin.get_catalog()

    assert response.metric_count == 3
    assert response.metric_names == [
        "smoke_cancel_metric",
        "smoke_file_metric",
        "smoke_table_metric",
    ]
    assert response.plugin_sources[0].source_kind == "directory"
    assert response.plugin_sources[0].source == smoke_plugin_uri()
    assert response.metric_queues == SMOKE_METRIC_QUEUES
    assert response.catalog_fingerprint
    assert SMOKE_PLUGIN_DIR.exists()


def test_workers_route_reports_inspect_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)
    snapshot = WorkerInspectSnapshot(
        inspect_available=True,
        active={
            "interactive": [
                {"id": "job-1", "name": "lyra.run_metric", "args": ["hidden"]}
            ]
        },
        reserved={"interactive": []},
        scheduled={"interactive": [{"eta": "2026-01-01T00:00:00Z"}]},
        stats={"interactive": {"pool": {"max-concurrency": 1}}},
        active_queues={"interactive": ["interactive"]},
    )
    monkeypatch.setattr(admin, "get_worker_inspect_snapshot", lambda: snapshot)

    response = admin.list_workers()
    detail = admin.get_worker("interactive")

    interactive = next(
        worker for worker in response.workers if worker.name == "interactive"
    )
    assert response.inspect_available is True
    assert interactive.status == "online"
    assert interactive.active_count == 1
    assert detail.active_tasks[0].id == "job-1"
    assert detail.active_tasks[0].name == "lyra.run_metric"
    assert "args" not in detail.active_tasks[0].model_dump()


def test_workers_route_handles_unknown_inspect_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)
    snapshot = WorkerInspectSnapshot(
        inspect_available=False,
        active=None,
        reserved=None,
        scheduled=None,
        stats=None,
        active_queues=None,
    )
    monkeypatch.setattr(admin, "get_worker_inspect_snapshot", lambda: snapshot)

    response = admin.list_workers()

    assert response.inspect_available is False
    assert {worker.status for worker in response.workers} == {"unknown"}


def test_worker_detail_returns_404_for_unknown_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin,
        "get_worker_inspect_snapshot",
        lambda: WorkerInspectSnapshot(
            inspect_available=True,
            active={},
            reserved={},
            scheduled={},
            stats={},
            active_queues={},
        ),
    )

    with pytest.raises(admin.HTTPException) as exc_info:
        admin.get_worker("missing")

    assert exc_info.value.status_code == 404


def test_queues_route_reports_assignments_and_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(
        tmp_path,
        monkeypatch,
        metric_queues={
            "smoke_table_metric": "interactive",
            "batch_metric": "batch",
        },
    )
    snapshot = WorkerInspectSnapshot(
        inspect_available=True,
        active={},
        reserved={},
        scheduled={},
        stats={},
        active_queues={"interactive": ["interactive"]},
    )
    monkeypatch.setattr(admin, "get_worker_inspect_snapshot", lambda: snapshot)

    response = admin.list_queues()

    queues = {queue.name: queue for queue in response.queues}
    assert queues["interactive"].is_default is True
    assert queues["interactive"].assigned_metric_count == 1
    assert queues["interactive"].configured_workers == ["interactive"]
    assert queues["interactive"].observed_workers == ["interactive"]
    assert queues["interactive"].pending_depth is None
    assert queues["interactive"].pending_depth_unknown is True
    assert queues["batch"].assigned_metric_count == 1
