import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI

from lyra_app import main


def test_lifespan_starts_and_stops_worker_inspect_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def start_worker_inspect_collector() -> None:
        calls.append("start")

    async def stop_worker_inspect_collector() -> None:
        calls.append("stop")

    async def run_lifespan() -> None:
        async with main.lifespan(FastAPI()):
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
) -> None:
    calls: list[str] = []

    async def start_worker_inspect_collector() -> None:
        calls.append("worker-start")

    async def stop_worker_inspect_collector() -> None:
        calls.append("worker-stop")

    @asynccontextmanager
    async def mcp_lifespan(_: FastAPI) -> AsyncIterator[None]:
        calls.append("mcp-start")
        try:
            yield
        finally:
            calls.append("mcp-stop")

    app = FastAPI()
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
