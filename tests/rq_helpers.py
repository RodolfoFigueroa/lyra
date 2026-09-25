"""Disposable Redis and production-launcher fixtures."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.models.plugin import PluginInfo
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import create_engine

from lyra_app import job_store, registry, worker_launcher
from lyra_app.config import initialize_runtime_config
from lyra_app.db import connection as database_connection
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.db.redis import configure_redis
from lyra_app.job_submission import submit_job
from tests.config_helpers import load_test_config
from tests.plugin_helpers import plugin_config
from tests.rq_metrics import create_plugin
from tests.smoke_plugin_helpers import feature_collection

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from multiprocessing.process import BaseProcess
    from pathlib import Path

    from lyra.sdk.models.job import JobCreateResponse
    from lyra.sdk.types import JsonObject

    from lyra_app.config import LyraConfig

REDIS_URL = os.environ.get("LYRA_TEST_REDIS_URL")


def eventually(predicate: Callable[[], bool], *, seconds: float = 25) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"Condition did not become true within {seconds} seconds")
        time.sleep(0.05)


@dataclass
class Backend:
    config: LyraConfig
    queue: Queue
    scratch: Path

    def submit(
        self,
        metric: str = "work",
        parameters: JsonObject | None = None,
        *,
        payload: JsonObject | None = None,
    ) -> JobCreateResponse:
        if payload is None:
            payload = {
                "location": {"data_type": "geojson", "value": feature_collection()}
            }
            if metric not in {"feature_report", "bounded_features"}:
                payload["parameters"] = parameters or {}

        async def run() -> JobCreateResponse:
            database = ApplicationDatabaseRuntime(self.config)
            try:
                await database.start()
                return await submit_job(
                    JobCreateRequest(metric=metric, input=payload), database=database
                )
            finally:
                await database.close()

        return asyncio.run(run())

    @staticmethod
    def terminal(job_id: str) -> bool:
        observation = job_store.read_job(job_id)
        return observation.snapshot is not None and observation.snapshot.status in {
            "failed",
            "succeeded",
        }


def _launch(config: LyraConfig) -> None:
    os.setsid()
    worker_launcher.launch_worker("test", config=config)


@contextmanager
def pool(backend: Backend) -> Iterator[BaseProcess]:
    process = multiprocessing.get_context("fork").Process(
        target=_launch, args=(backend.config,)
    )
    process.start()
    try:
        yield process
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if process.is_alive():
            for pid_file in backend.scratch.glob("*/pid"):
                with suppress(ProcessLookupError):
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
            if process.pid is not None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.join(timeout=10)
        if process.pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        assert not process.is_alive()
        process.close()


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Backend]:
    if not REDIS_URL:
        pytest.skip("Set LYRA_TEST_REDIS_URL to a disposable Redis service.")
    name = f"lyra-test-{uuid4().hex}"
    connection = Redis.from_url(REDIS_URL, socket_timeout=5)
    connection.ping()
    manifest = create_plugin().manifest(
        plugin=PluginInfo(name="rq-fixture", version="1.0.0"),
        factory="tests.rq_metrics:create_plugin",
    )
    (tmp_path / "lyra.plugin.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    config = load_test_config(tmp_path)
    config.redis.url = REDIS_URL
    config.plugins.allowed_queues = [name]
    config.plugins.default_queue = name
    config.plugins.installed = [plugin_config(tmp_path)]
    worker = config.workers["interactive"].model_copy(
        update={"queues": [name], "temp_dir": tmp_path / "scratch"}
    )
    config.workers = {"test": worker}
    initialize_runtime_config(config)
    configure_redis(config)
    registry.initialize_catalog(config)
    # Exercise real SQL connections locally without requiring PostgreSQL or EE in CI.
    monkeypatch.setattr(
        database_connection,
        "create_sync_database_engine",
        lambda *_: create_engine("sqlite://"),
    )
    monkeypatch.setattr(worker_launcher, "initialize_earth_engine", lambda _: None)
    queue = Queue(name, connection=connection, serializer=JSONSerializer)
    yield Backend(config, queue, tmp_path / "scratch")
    queue.delete(delete_jobs=True)
    connection.close()
    registry.reset_catalog()
