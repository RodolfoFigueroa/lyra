import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast

import pytest
from fastapi import HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from lyra_app.db import connection
from lyra_app.routes import met_zone
from tests.config_helpers import load_test_config


def test_application_database_runtime_owns_async_and_spatial_engines(
    tmp_path: Path,
) -> None:
    config = load_test_config(tmp_path)
    runtime = connection.ApplicationDatabaseRuntime(config)

    async def exercise() -> int:
        await runtime.start()
        assert runtime.require_async_engine().url.drivername == "postgresql+psycopg"
        assert runtime.require_spatial_engine().url.drivername == "postgresql+psycopg"
        result = await runtime.run_spatial(lambda value: value + 1, 2)
        await runtime.close()
        return result

    assert asyncio.run(exercise()) == 3
    with pytest.raises(RuntimeError, match="has not been started"):
        runtime.require_async_engine()


def test_engine_factory_applies_bounded_pool_and_postgres_timeouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    captured: dict[str, Any] = {}
    sentinel = cast("Engine", object())

    def create_engine(url: object, **kwargs: object) -> Engine:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(connection, "create_engine", create_engine)

    result = connection.create_sync_database_engine(config.database.spatial, config)

    assert result is sentinel
    assert captured["pool_size"] == 2
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 2.0
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "connect_timeout": 5,
        "options": "-c statement_timeout=25000",
    }


def test_worker_engine_is_recreated_after_process_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    connection.dispose_worker_engine()
    process_id = 100
    engines: list[Any] = []

    class FakeEngine:
        def __init__(self) -> None:
            self.dispose_calls: list[bool] = []

        def dispose(self, *, close: bool = True) -> None:
            self.dispose_calls.append(close)

    def create_engine(*_: object) -> Engine:
        engine = FakeEngine()
        engines.append(engine)
        return cast("Engine", engine)

    monkeypatch.setattr(connection, "create_sync_database_engine", create_engine)
    monkeypatch.setattr(connection.os, "getpid", lambda: process_id)

    first = connection.get_worker_engine(config)
    assert connection.get_worker_engine(config) is first
    process_id = 101
    second = connection.get_worker_engine(config)

    assert second is not first
    assert engines[0].dispose_calls == [False]
    connection.dispose_worker_engine()
    assert engines[1].dispose_calls == [True]


def test_worker_database_probe_executes_query_and_disposes_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    statements: list[str] = []
    disposed: list[bool] = []

    class FakeConnection:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, statement: object) -> None:
            statements.append(str(statement))

    class FakeEngine:
        def connect(self) -> FakeConnection:
            return FakeConnection()

        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(
        connection,
        "create_sync_database_engine",
        lambda _pool, _runtime_config: cast("Engine", FakeEngine()),
    )

    connection.probe_worker_database(config)

    assert statements == ["SELECT 1"]
    assert disposed == [True]


def test_worker_database_probe_disposes_engine_when_connection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    disposed: list[bool] = []

    class FailedEngine:
        def connect(self) -> None:
            statement = "connect"
            message = "unavailable"
            raise OperationalError(statement, {}, Exception(message))

        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(
        connection,
        "create_sync_database_engine",
        lambda _pool, _runtime_config: cast("Engine", FailedEngine()),
    )

    with pytest.raises(OperationalError):
        connection.probe_worker_database(config)

    assert disposed == [True]


def test_met_zone_lookup_returns_retryable_503_for_database_failure(
    tmp_path: Path,
) -> None:
    config = load_test_config(tmp_path)

    class FailedConnectionContext:
        async def __aenter__(self) -> None:
            statement = "connect"
            message = "unavailable"
            raise OperationalError(statement, {}, Exception(message))

        async def __aexit__(self, *_: object) -> None:
            return None

    database = cast(
        "connection.ApplicationDatabaseRuntime",
        SimpleNamespace(
            config=config,
            require_async_engine=lambda: SimpleNamespace(
                connect=FailedConnectionContext
            ),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(met_zone.get_met_zone_code("Guadalajara", database))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "database_unavailable",
        "message": "The spatial database is temporarily unavailable.",
        "retryable": True,
    }
    assert exc_info.value.headers == {"Retry-After": "5"}
