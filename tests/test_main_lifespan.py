import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from lyra_app import main
from lyra_app.db.connection import ApplicationDatabaseRuntime
from tests.config_helpers import load_test_config


def test_lifespan_starts_and_stops_worker_inspect_collector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def start_worker_inspect_collector() -> None:
        calls.append("start")

    async def stop_worker_inspect_collector() -> None:  # ruff: ignore[unused-async]
        calls.append("stop")

    app = FastAPI()
    app.state.database = ApplicationDatabaseRuntime(load_test_config(tmp_path))

    async def run_lifespan() -> None:
        async with main.lifespan(app):
            assert calls == ["start"]

    monkeypatch.setattr(
        main,
        "start_worker_inspect_collector",
        start_worker_inspect_collector,
    )
    monkeypatch.setattr(
        main,
        "stop_worker_inspect_collector",
        stop_worker_inspect_collector,
    )

    asyncio.run(run_lifespan())

    assert calls == ["start", "stop"]


def test_lifespan_owns_mounted_mcp_session_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def start_worker_inspect_collector() -> None:
        calls.append("worker-start")

    async def stop_worker_inspect_collector() -> None:  # ruff: ignore[unused-async]
        calls.append("worker-stop")

    @asynccontextmanager
    async def mcp_lifespan(_: FastAPI) -> AsyncIterator[None]:
        calls.append("mcp-start")
        try:
            yield
        finally:
            calls.append("mcp-stop")

    app = FastAPI()
    app.state.database = ApplicationDatabaseRuntime(load_test_config(tmp_path))
    app.state.mcp_app = FastAPI(lifespan=mcp_lifespan)
    monkeypatch.setattr(
        main,
        "start_worker_inspect_collector",
        start_worker_inspect_collector,
    )
    monkeypatch.setattr(
        main,
        "stop_worker_inspect_collector",
        stop_worker_inspect_collector,
    )

    async def run_lifespan() -> None:
        async with main.lifespan(app):
            assert calls == ["worker-start", "mcp-start"]

    asyncio.run(run_lifespan())

    assert calls == ["worker-start", "mcp-start", "mcp-stop", "worker-stop"]


def test_lifespan_owns_database_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeDatabaseRuntime:
        @staticmethod
        async def start() -> None:
            calls.append("database-start")

        @staticmethod
        async def close() -> None:
            calls.append("database-close")

    def start_worker_inspect_collector() -> None:
        calls.append("worker-start")

    async def stop_worker_inspect_collector() -> None:  # ruff: ignore[unused-async]
        calls.append("worker-stop")

    app = FastAPI()
    app.state.database = FakeDatabaseRuntime()
    monkeypatch.setattr(
        main,
        "start_worker_inspect_collector",
        start_worker_inspect_collector,
    )
    monkeypatch.setattr(
        main,
        "stop_worker_inspect_collector",
        stop_worker_inspect_collector,
    )

    async def run_lifespan() -> None:
        async with main.lifespan(app):
            assert calls == ["database-start", "worker-start"]

    asyncio.run(run_lifespan())

    assert calls == [
        "database-start",
        "worker-start",
        "worker-stop",
        "database-close",
    ]


def test_run_server_configures_trusted_proxy_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    config.api.forwarded_allow_ips = ["127.0.0.1", "172.20.0.0/16"]
    app = FastAPI()
    calls: dict[str, object] = {}

    def create_app(runtime_config: object) -> FastAPI:
        calls["config"] = runtime_config
        return app

    def run(server_app: FastAPI, **kwargs: object) -> None:
        calls["app"] = server_app
        calls["kwargs"] = kwargs

    monkeypatch.setattr(main, "create_app", create_app)
    monkeypatch.setattr(main.uvicorn, "run", run)

    main.run_server(config)

    assert calls == {
        "config": config,
        "app": app,
        "kwargs": {
            "host": config.api.host,
            "port": config.api.port,
            "reload": False,
            "proxy_headers": True,
            "forwarded_allow_ips": ["127.0.0.1", "172.20.0.0/16"],
        },
    }


@pytest.mark.parametrize("failure_stage", ["database", "collector", "mcp", "body"])
def test_lifespan_closes_database_on_failure(
    failure_stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ApplicationDatabaseRuntime(load_test_config(tmp_path))
    app = FastAPI()
    app.state.database = runtime
    start = runtime.start
    close = AsyncMock(wraps=runtime.close)
    monkeypatch.setattr(runtime, "close", close)
    stop_collector = AsyncMock()
    monkeypatch.setattr(main, "stop_worker_inspect_collector", stop_collector)

    async def start_database() -> None:
        await start()
        if failure_stage == "database":
            raise RuntimeError(failure_stage)

    def start_collector() -> None:
        if failure_stage == "collector":
            raise RuntimeError(failure_stage)

    @asynccontextmanager
    async def mcp_lifespan(_: FastAPI) -> AsyncIterator[None]:
        if failure_stage == "mcp":
            raise RuntimeError(failure_stage)
        yield

    monkeypatch.setattr(runtime, "start", start_database)
    monkeypatch.setattr(main, "start_worker_inspect_collector", start_collector)
    app.state.mcp_app = FastAPI(lifespan=mcp_lifespan)

    async def exercise() -> None:
        message = "body"
        with pytest.raises(RuntimeError, match=failure_stage):
            async with main.lifespan(app):
                raise RuntimeError(message)

    asyncio.run(exercise())
    close.assert_awaited_once()
    assert runtime.async_engine is None
    assert runtime.spatial_engine is None
    if failure_stage in {"mcp", "body"}:
        stop_collector.assert_awaited_once()


def test_lifespan_rejects_missing_database_runtime() -> None:
    async def exercise() -> None:
        with pytest.raises(
            RuntimeError, match="Application database runtime is unavailable"
        ):
            async with main.lifespan(FastAPI()):
                pytest.fail("Lifespan must not start without its database runtime")

    asyncio.run(exercise())
