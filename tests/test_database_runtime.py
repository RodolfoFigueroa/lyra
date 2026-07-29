import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast

import pytest
from fastapi import HTTPException
from lyra.sdk.postgres_connection import PostgresWorkload
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from lyra_app.db import connection
from lyra_app.routes import met_zone
from tests.config_helpers import load_test_config


class FakeAsyncEngine:
    def __init__(
        self,
        events: list[str],
        *,
        dispose_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.dispose_error = dispose_error

    async def dispose(self) -> None:
        self.events.append("async-dispose")
        if self.dispose_error is not None:
            raise self.dispose_error


class FakeSpatialEngine:
    def __init__(
        self,
        events: list[str],
        *,
        dispose_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.dispose_error = dispose_error

    def dispose(self) -> None:
        self.events.append("spatial-dispose")
        if self.dispose_error is not None:
            raise self.dispose_error


class FakeExecutor:
    def __init__(
        self,
        events: list[str],
        *,
        shutdown_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.shutdown_error = shutdown_error

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        assert wait is True
        assert cancel_futures is True
        self.events.append("executor-shutdown")
        if self.shutdown_error is not None:
            raise self.shutdown_error


class OneShotConstructionFailure:
    def __init__(self, stage: str | None) -> None:
        self.stage = stage

    def raise_for(self, stage: str) -> None:
        if self.stage == stage:
            self.stage = None
            msg = f"{stage} construction failed"
            raise RuntimeError(msg)


def install_runtime_resource_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_stage: str | None = None,
    cleanup_fails: bool = False,
) -> tuple[list[str], list[object]]:
    events: list[str] = []
    created: list[object] = []
    failure = OneShotConstructionFailure(fail_stage)
    semaphore_type = asyncio.Semaphore

    def create_async(*_: object, **__: object) -> AsyncEngine:
        failure.raise_for("async")
        error = RuntimeError("async cleanup failed") if cleanup_fails else None
        engine = FakeAsyncEngine(events, dispose_error=error)
        created.append(engine)
        return cast("AsyncEngine", engine)

    def create_spatial(*_: object, **__: object) -> Engine:
        failure.raise_for("spatial")
        error = RuntimeError("spatial cleanup failed") if cleanup_fails else None
        engine = FakeSpatialEngine(events, dispose_error=error)
        created.append(engine)
        return cast("Engine", engine)

    def create_executor(**_: object) -> FakeExecutor:
        failure.raise_for("executor")
        error = RuntimeError("executor cleanup failed") if cleanup_fails else None
        executor = FakeExecutor(events, shutdown_error=error)
        created.append(executor)
        return executor

    def create_semaphore(_: int) -> asyncio.Semaphore:
        failure.raise_for("semaphore")
        semaphore = semaphore_type(1)
        created.append(semaphore)
        return semaphore

    monkeypatch.setattr(connection, "create_async_database_engine", create_async)
    monkeypatch.setattr(connection, "create_sync_database_engine", create_spatial)
    monkeypatch.setattr(connection, "ThreadPoolExecutor", create_executor)
    monkeypatch.setattr(connection.asyncio, "Semaphore", create_semaphore)
    return events, created


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


@pytest.mark.parametrize(
    ("fail_stage", "cleanup_events"),
    [
        ("async", []),
        ("spatial", ["async-dispose"]),
        ("executor", ["spatial-dispose", "async-dispose"]),
        (
            "semaphore",
            ["executor-shutdown", "spatial-dispose", "async-dispose"],
        ),
    ],
)
def test_runtime_start_failure_is_atomic_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_stage: str,
    cleanup_events: list[str],
) -> None:
    config = load_test_config(tmp_path)
    events, created = install_runtime_resource_fakes(
        monkeypatch,
        fail_stage=fail_stage,
    )
    runtime = connection.ApplicationDatabaseRuntime(config)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match=f"{fail_stage} construction failed"):
            await runtime.start()

        assert runtime.async_engine is None
        assert runtime.spatial_engine is None
        assert vars(runtime)["_spatial_executor"] is None
        assert vars(runtime)["_spatial_capacity"] is None
        assert events == cleanup_events

        await runtime.start()
        started_resources = list(created)
        await runtime.start()
        assert created == started_resources

        await runtime.close()
        await runtime.close()

    asyncio.run(exercise())

    assert events == [
        *cleanup_events,
        "executor-shutdown",
        "spatial-dispose",
        "async-dispose",
    ]


def test_runtime_concurrent_starts_create_one_resource_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    events, created = install_runtime_resource_fakes(monkeypatch)
    runtime = connection.ApplicationDatabaseRuntime(config)

    async def exercise() -> None:
        await asyncio.gather(runtime.start(), runtime.start())
        assert len(created) == 4
        await runtime.close()

    asyncio.run(exercise())
    assert events == [
        "executor-shutdown",
        "spatial-dispose",
        "async-dispose",
    ]


def test_runtime_close_attempts_every_cleanup_and_clears_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    events, _ = install_runtime_resource_fakes(monkeypatch, cleanup_fails=True)
    runtime = connection.ApplicationDatabaseRuntime(config)

    async def exercise() -> None:
        await runtime.start()
        with pytest.raises(
            BaseExceptionGroup,
            match="Multiple failures occurred",
        ) as exc_info:
            await runtime.close()

        assert len(exc_info.value.exceptions) == 3
        assert runtime.async_engine is None
        assert runtime.spatial_engine is None
        assert vars(runtime)["_spatial_executor"] is None
        assert vars(runtime)["_spatial_capacity"] is None
        await runtime.close()

    asyncio.run(exercise())
    assert events == [
        "executor-shutdown",
        "spatial-dispose",
        "async-dispose",
    ]


def test_runtime_start_preserves_construction_error_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    events, _ = install_runtime_resource_fakes(
        monkeypatch,
        fail_stage="semaphore",
        cleanup_fails=True,
    )
    runtime = connection.ApplicationDatabaseRuntime(config)

    with pytest.raises(RuntimeError, match="semaphore construction failed") as exc_info:
        asyncio.run(runtime.start())

    assert len(exc_info.value.__notes__) == 3
    cleanup_notes = "\n".join(exc_info.value.__notes__)
    assert "executor cleanup failed" in cleanup_notes
    assert "spatial cleanup failed" in cleanup_notes
    assert "async cleanup failed" in cleanup_notes
    assert events == [
        "executor-shutdown",
        "spatial-dispose",
        "async-dispose",
    ]


def test_sync_engine_factory_applies_read_only_spatial_profile(
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

    result = connection.create_sync_database_engine(
        config.database.spatial,
        workload=PostgresWorkload.SPATIAL,
        config=config,
    )

    assert result is sentinel
    url = cast("URL", captured["url"])
    assert url.query["application_name"] == "lyra-spatial"
    assert url.query["options"] == (
        "-c default_transaction_read_only=on -c statement_timeout=25000"
    )
    assert captured["pool_size"] == 2
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == pytest.approx(2.0)
    assert captured["pool_recycle"] == 900
    assert captured["pool_pre_ping"] is True
    assert captured["hide_parameters"] is True
    assert captured["connect_args"] == {
        "connect_timeout": 5,
    }


def test_async_engine_factory_preserves_libpq_url_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    captured: dict[str, Any] = {}
    sentinel = cast("AsyncEngine", object())
    database_password = config.database.read_password()
    configured_url = URL.create(
        "postgresql+psycopg",
        username="lyra",
        password=database_password,
        host="postgres",
        database="lyra",
        query={
            "options": "-c lock_timeout=4000",
            "sslmode": "verify-full",
            "sslrootcert": "/run/secrets/postgres-ca.pem",
        },
    )

    def create_engine(url: object, **kwargs: object) -> AsyncEngine:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(connection, "database_url", lambda **_: configured_url)
    monkeypatch.setattr(connection, "create_async_engine", create_engine)

    result = connection.create_async_database_engine(
        config.database.api,
        workload=PostgresWorkload.API,
        config=config,
    )

    assert result is sentinel
    url = cast("URL", captured["url"])
    assert url.query["application_name"] == "lyra-api"
    assert url.query["sslmode"] == "verify-full"
    assert url.query["sslrootcert"] == "/run/secrets/postgres-ca.pem"
    assert url.query["options"] == (
        "-c lock_timeout=4000 "
        "-c default_transaction_read_only=on "
        "-c statement_timeout=10000"
    )
    assert captured["pool_size"] == 5
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == pytest.approx(2.0)
    assert captured["pool_recycle"] == 900
    assert captured["pool_pre_ping"] is True
    assert captured["hide_parameters"] is True
    assert captured["connect_args"] == {"connect_timeout": 5}


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

    def create_engine(*_: object, **__: object) -> Engine:
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

        @staticmethod
        def execute(statement: object) -> None:
            statements.append(str(statement))

    class FakeEngine:
        @staticmethod
        def connect() -> FakeConnection:
            return FakeConnection()

        @staticmethod
        def dispose() -> None:
            disposed.append(True)

    monkeypatch.setattr(
        connection,
        "create_sync_database_engine",
        lambda _pool, **_kwargs: cast("Engine", FakeEngine()),
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
        @staticmethod
        def connect() -> None:
            statement = "connect"
            message = "unavailable"
            raise OperationalError(statement, {}, Exception(message))

        @staticmethod
        def dispose() -> None:
            disposed.append(True)

    monkeypatch.setattr(
        connection,
        "create_sync_database_engine",
        lambda _pool, **_kwargs: cast("Engine", FailedEngine()),
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
