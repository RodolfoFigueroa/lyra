import asyncio

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
