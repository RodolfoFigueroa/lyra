import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast

import pytest
from fastapi import Response
from lyra.sdk.models.observability import DatabaseHealth
from redis.exceptions import RedisError

from lyra_app.config import clear_config_cache
from lyra_app.registry import initialize_catalog, reset_catalog
from lyra_app.routes import admin, health
from tests.config_helpers import load_test_config
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
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
    plugins: list[Path] | None = None,
) -> None:
    load_test_config(tmp_path, metric_queues=metric_queues, plugins=plugins)
    if metric_queues and plugins is None:
        monkeypatch.setattr(admin, "get_loaded_metric_queues", lambda: metric_queues)


def test_readiness_reports_healthy_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "redis_client", FakeRedisAsync())
    monkeypatch.setattr(health, "is_catalog_loaded", lambda: True)

    async def database_health(  # ruff: ignore[unused-async] -- awaited double
        *_: object,
    ) -> DatabaseHealth:
        return DatabaseHealth(status="ok")

    monkeypatch.setattr(health, "database_health", database_health)
    database = cast(
        "Any",
        SimpleNamespace(
            config=SimpleNamespace(
                database=SimpleNamespace(readiness_timeout_seconds=1.0)
            )
        ),
    )
    http_response = Response()

    response = asyncio.run(health.readiness(http_response, database))

    assert http_response.status_code == 200
    assert http_response.headers["Cache-Control"] == "no-store"
    assert response.status == "ready"
    assert response.redis.status == "ok"
    assert response.database.status == "ok"


def test_readiness_reports_unavailable_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "redis_client", FakeRedisAsync(fail=True))

    async def database_health(  # ruff: ignore[unused-async] -- awaited double
        *_: object,
    ) -> DatabaseHealth:
        return DatabaseHealth(status="ok")

    monkeypatch.setattr(health, "database_health", database_health)
    database = cast(
        "Any",
        SimpleNamespace(
            config=SimpleNamespace(
                database=SimpleNamespace(readiness_timeout_seconds=1.0)
            )
        ),
    )
    http_response = Response()

    response = asyncio.run(health.readiness(http_response, database))

    assert http_response.status_code == 503
    assert response.status == "not_ready"
    assert response.redis.status == "unavailable"


def test_stalled_database_check_does_not_block_liveness() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowConnectionContext:
        async def __aenter__(self) -> Self:
            entered.set()
            await release.wait()
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        @staticmethod
        async def execute(*_: object) -> None:
            return None

    database = cast(
        "Any",
        SimpleNamespace(
            require_async_engine=lambda: SimpleNamespace(connect=SlowConnectionContext)
        ),
    )

    async def exercise() -> None:
        database_check = asyncio.create_task(
            health.database_health(database, timeout_seconds=10.0)
        )
        await entered.wait()
        live = await asyncio.wait_for(health.liveness(), timeout=0.1)
        assert live.status == "ok"
        release.set()
        await database_check

    asyncio.run(exercise())


def test_admin_status_excludes_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(tmp_path, monkeypatch)
    monkeypatch.setattr(admin, "get_sync_client", FakeRedisSync)

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
    assert response.installed_plugins == []
    assert response.metric_queues == {}


def test_catalog_metadata_reports_smoke_directory_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_admin(
        tmp_path,
        monkeypatch,
        metric_queues=SMOKE_METRIC_QUEUES,
        plugins=[SMOKE_PLUGIN_DIR],
    )
    initialize_catalog()

    response = admin.get_catalog()

    assert response.metric_count == 3
    assert response.metric_names == [
        "smoke_file_metric",
        "smoke_progress_metric",
        "smoke_table_metric",
    ]
    assert response.installed_plugins[0].distribution == "lyra-smoke-plugin"
    assert response.installed_plugins[0].version == "0.1.0"
    assert response.metric_queues == SMOKE_METRIC_QUEUES
    assert response.catalog_fingerprint
    assert SMOKE_PLUGIN_DIR.exists()
