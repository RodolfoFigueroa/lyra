import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from redis.exceptions import RedisError

from lyra_app import worker_control
from lyra_app.config import clear_config_cache, get_config
from lyra_app.registry import refresh_catalog, reset_catalog
from lyra_app.routes import admin, health
from lyra_app.worker_control import WorkerInspectSnapshot, WorkerInspectState
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


def _inspect_state(
    snapshot: WorkerInspectSnapshot,
    *,
    observed_at: datetime | None = datetime(2026, 1, 1, tzinfo=UTC),
    age_seconds: float | None = 0.5,
    stale: bool = False,
    last_error: str | None = None,
) -> WorkerInspectState:
    return WorkerInspectState(
        snapshot=snapshot,
        observed_at=observed_at,
        age_seconds=age_seconds,
        stale=stale,
        last_error=last_error,
    )


@pytest.fixture(autouse=True)
def _reset_catalog() -> Iterator[None]:
    worker_control.reset_worker_inspect_collector_state()
    reset_catalog()
    yield
    worker_control.reset_worker_inspect_collector_state()
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
    monkeypatch.setattr(
        admin, "get_worker_inspect_state", lambda: _inspect_state(snapshot)
    )

    response = admin.list_workers()
    detail = admin.get_worker("interactive")

    interactive = next(
        worker for worker in response.workers if worker.name == "interactive"
    )
    assert response.inspect_available is True
    assert response.inspect_metadata.stale is False
    assert detail.inspect_metadata.observed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert interactive.status == "online"
    assert interactive.active_count == 1
    assert detail.active_tasks[0].id == "job-1"
    assert detail.active_tasks[0].name == "lyra.run_metric"
    assert "args" not in detail.active_tasks[0].model_dump()


def test_workers_route_matches_named_celery_worker_to_configured_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)
    snapshot = WorkerInspectSnapshot(
        inspect_available=True,
        active={
            "interactive@worker-host": [
                {"id": "job-1", "name": "lyra.run_metric", "args": ["hidden"]}
            ]
        },
        reserved={"interactive@worker-host": []},
        scheduled={"interactive@worker-host": []},
        stats={"interactive@worker-host": {"hostname": "interactive@worker-host"}},
        active_queues={"interactive@worker-host": ["interactive"]},
    )
    monkeypatch.setattr(
        admin, "get_worker_inspect_state", lambda: _inspect_state(snapshot)
    )

    response = admin.list_workers()
    detail = admin.get_worker("interactive")

    workers = {worker.name: worker for worker in response.workers}
    assert "interactive@worker-host" not in workers
    assert workers["interactive"].configured is True
    assert workers["interactive"].observed is True
    assert workers["interactive"].status == "online"
    assert workers["interactive"].active_count == 1
    assert detail.active_tasks[0].worker == "interactive"
    assert detail.active_tasks[0].id == "job-1"
    assert detail.stats == {"hostname": "interactive@worker-host"}


def test_workers_route_keeps_default_celery_worker_names_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)
    snapshot = WorkerInspectSnapshot(
        inspect_available=True,
        active={},
        reserved={},
        scheduled={},
        stats={"celery@worker-host": {"hostname": "celery@worker-host"}},
        active_queues={"celery@worker-host": ["interactive"]},
    )
    monkeypatch.setattr(
        admin, "get_worker_inspect_state", lambda: _inspect_state(snapshot)
    )

    response = admin.list_workers()

    workers = {worker.name: worker for worker in response.workers}
    assert workers["celery@worker-host"].configured is False
    assert workers["celery@worker-host"].observed is True
    assert workers["celery@worker-host"].status == "online"


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
    monkeypatch.setattr(
        admin,
        "get_worker_inspect_state",
        lambda: _inspect_state(
            snapshot,
            observed_at=None,
            age_seconds=None,
            stale=True,
        ),
    )

    response = admin.list_workers()

    assert response.inspect_available is False
    assert response.inspect_metadata.observed_at is None
    assert response.inspect_metadata.age_seconds is None
    assert response.inspect_metadata.stale is True
    assert {worker.status for worker in response.workers} == {"unknown"}


def test_worker_detail_returns_404_for_unknown_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)
    monkeypatch.setattr(
        admin,
        "get_worker_inspect_state",
        lambda: _inspect_state(
            WorkerInspectSnapshot(
                inspect_available=True,
                active={},
                reserved={},
                scheduled={},
                stats={},
                active_queues={},
            )
        ),
    )

    with pytest.raises(admin.HTTPException) as exc_info:
        admin.get_worker("missing")

    assert exc_info.value.status_code == 404


def test_worker_and_queue_routes_use_background_state_without_live_inspect(
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
        scheduled={"interactive": []},
        stats={"interactive": {"hostname": "interactive"}},
        active_queues={"interactive": ["interactive"]},
    )

    monkeypatch.setattr(worker_control, "inspect_workers", lambda: snapshot)
    asyncio.run(worker_control.refresh_worker_inspect_snapshot())

    def fail_live_inspect() -> WorkerInspectSnapshot:
        message = "unexpected live inspect"
        raise AssertionError(message)

    monkeypatch.setattr(worker_control, "inspect_workers", fail_live_inspect)

    workers = admin.list_workers()
    detail = admin.get_worker("interactive")
    queues = admin.list_queues()
    workers_by_name = {worker.name: worker for worker in workers.workers}
    queues_by_name = {queue.name: queue for queue in queues.queues}

    assert workers_by_name["interactive"].status == "online"
    assert detail.active_tasks[0].id == "job-1"
    assert queues_by_name["interactive"].observed_workers == ["interactive"]


def test_worker_and_queue_routes_degrade_on_cold_background_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)

    def fail_live_inspect() -> WorkerInspectSnapshot:
        message = "unexpected live inspect"
        raise AssertionError(message)

    monkeypatch.setattr(worker_control, "inspect_workers", fail_live_inspect)

    workers = admin.list_workers()
    queues = admin.list_queues()

    assert workers.inspect_available is False
    assert workers.inspect_metadata.observed_at is None
    assert workers.inspect_metadata.stale is True
    assert {worker.status for worker in workers.workers} == {"unknown"}
    assert queues.inspect_metadata.observed_at is None
    assert queues.inspect_metadata.stale is True


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
    monkeypatch.setattr(
        admin, "get_worker_inspect_state", lambda: _inspect_state(snapshot)
    )

    response = admin.list_queues()

    queues = {queue.name: queue for queue in response.queues}
    assert queues["interactive"].is_default is True
    assert queues["interactive"].assigned_metric_count == 1
    assert queues["interactive"].configured_workers == ["interactive"]
    assert queues["interactive"].observed_workers == ["interactive"]
    assert queues["interactive"].pending_depth is None
    assert queues["interactive"].pending_depth_unknown is True
    assert queues["batch"].assigned_metric_count == 1
    assert response.inspect_metadata.stale is False


def test_queues_route_matches_named_celery_consumers_to_configured_workers(
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
        active_queues={
            "interactive@worker-host": ["interactive"],
            "celery@legacy-host": ["batch"],
        },
    )
    monkeypatch.setattr(
        admin, "get_worker_inspect_state", lambda: _inspect_state(snapshot)
    )

    response = admin.list_queues()

    queues = {queue.name: queue for queue in response.queues}
    assert queues["interactive"].observed_workers == ["interactive"]
    assert queues["batch"].observed_workers == ["celery@legacy-host"]
