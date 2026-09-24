"""Real HTTP/file regressions; deadlines allow 0.5s of scheduling tolerance."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import aiohttp
import pandas as pd
import pytest
from lyra.api import (
    AsyncJobHandle,
    AsyncLyraClient,
    DownloadError,
    JobHandle,
    JobWaitTimeoutError,
    LyraClient,
)
from lyra.api.client.results import dataframe_path

from tests.test_api_client_jobs import _job_response, _result_response, _status_response
from tests.test_client_policies import resolve

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@dataclass
class HTTPState:
    host: str = ""
    mode: str = "success"
    calls: list[tuple[str, str]] = field(default_factory=list)
    headers: list[str | None] = field(default_factory=list)
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    table: bytes = b'{"value":6}\n{"value":7}\n'
    status_requests: int = 0


class HTTPHandler(BaseHTTPRequestHandler):
    state: HTTPState

    def log_message(self, *_args: object, **_kwargs: object) -> None:
        pass

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.respond(json.dumps(_job_response()).encode(), 202)

    def do_GET(self) -> None:
        is_status = self.path == "/jobs/job-1"
        if is_status:
            self.state.status_requests += 1
        delayed = (
            (is_status and self.state.mode in {"headers", "body"})
            or (self.path.endswith("/result") and self.state.mode == "result")
            or (self.path.endswith(".jsonl") and self.state.mode == "download")
            or (
                is_status
                and self.state.mode == "retry"
                and self.state.status_requests == 1
            )
        )
        payload = (
            _status_response()
            | {
                "status": "succeeded",
                "progress": {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "stage": "work",
                    "current": 1,
                },
            }
            if is_status
            else _result_response()
        )
        data = (
            self.state.table
            if self.path.endswith(".jsonl")
            else json.dumps(payload).encode()
        )
        self.respond(data, 401 if self.state.mode == "error" else 200, delayed=delayed)

    def respond(self, data: bytes, code: int, *, delayed: bool = False) -> None:
        self.state.calls.append((self.command, self.path))
        self.state.headers.append(self.headers.get("Authorization"))
        if delayed:
            self.state.entered.set()
        try:
            self.send_body(data, code, delayed=delayed)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if delayed:
                self.state.finished.set()

    def send_body(self, data: bytes, code: int, *, delayed: bool) -> None:
        if delayed and self.state.mode != "body":
            self.state.release.wait(10)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if delayed and self.state.mode == "body":
            for byte in data:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                if self.state.release.wait(0.03):
                    break
        else:
            self.wfile.write(data)


@pytest.fixture
def http_server() -> Iterator[HTTPState]:
    state = HTTPState()
    handler = type("Handler", (HTTPHandler,), {"state": state})
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        state.host = f"127.0.0.1:{server.server_port}"
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        thread.start()
        try:
            yield state
        finally:
            state.release.set()
            server.shutdown()
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.fixture(autouse=True)
def closed_http(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[aiohttp.ClientTimeout]]:
    sessions: list[aiohttp.ClientSession] = []
    connectors: list[aiohttp.BaseConnector] = []
    responses: list[aiohttp.ClientResponse] = []
    timeouts: list[aiohttp.ClientTimeout] = []
    original = aiohttp.ClientSession

    async def response_received(
        _session: aiohttp.ClientSession,
        _context: object,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        responses.append(params.response)
        await asyncio.sleep(0)

    def session(*, timeout: aiohttp.ClientTimeout) -> aiohttp.ClientSession:
        trace = aiohttp.TraceConfig()
        trace.on_request_end.append(response_received)
        created = original(timeout=timeout, trace_configs=[trace])
        sessions.append(created)
        assert created.connector is not None
        connectors.append(created.connector)
        timeouts.append(created.timeout)
        return created

    monkeypatch.setattr(aiohttp, "ClientSession", session)
    yield timeouts
    for thread in threading.enumerate():
        if thread.name == "lyra-polling":
            thread.join(timeout=2)
            assert not thread.is_alive()
    assert all(session.closed for session in sessions)
    assert all(connector.closed for connector in connectors)
    assert all(response.closed for response in responses)


def make_handle(
    state: HTTPState, *, asynchronous: bool, seconds: float = 30
) -> JobHandle | AsyncJobHandle:
    cls = AsyncLyraClient if asynchronous else LyraClient
    client = cls(state.host, timeout=seconds, secure=False, agent_api_key="secret")
    return resolve(client.raw.submit("heavy_metric", {}))


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("mode", ["headers", "body", "result"])
def test_real_observation_deadlines(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    *,
    asynchronous: bool,
    mode: str,
) -> None:
    http_server.mode = mode
    handle = make_handle(http_server, asynchronous=asynchronous)
    start = time.monotonic()
    with pytest.raises(JobWaitTimeoutError):
        resolve(handle.wait(timeout=0.15))
    assert 0.12 <= time.monotonic() - start < 0.65
    assert http_server.entered.is_set()
    if mode != "body":
        assert not http_server.finished.is_set()
    assert len(http_server.calls) == (3 if mode == "result" else 2)
    assert all(value == "Bearer secret" for value in http_server.headers)
    assert closed_http[-1].ceil_threshold == float("inf")


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_zero_unlimited_and_repeated_waits(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    *,
    asynchronous: bool,
) -> None:
    handle = make_handle(http_server, asynchronous=asynchronous)
    with pytest.raises(JobWaitTimeoutError):
        resolve(handle.wait(timeout=0))
    assert len(http_server.calls) == 1
    for _ in range(2):
        assert resolve(handle.wait(timeout=None)).status == "succeeded"
    assert len(http_server.calls) == 5
    assert all(timeout.total == 30 for timeout in closed_http)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_request_timeout_retries_before_job_deadline(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    *,
    asynchronous: bool,
) -> None:
    http_server.mode = "retry"
    handle = make_handle(http_server, asynchronous=asynchronous, seconds=0.1)
    assert resolve(handle.wait(timeout=2)).status == "succeeded"
    assert http_server.status_requests == 2
    assert len(closed_http) >= 3


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_permanent_http_error_closes_observation(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    *,
    asynchronous: bool,
) -> None:
    http_server.mode = "error"
    with pytest.raises(DownloadError):
        resolve(make_handle(http_server, asynchronous=asynchronous).wait())
    assert len(http_server.calls) == 2
    assert closed_http


def test_sync_wait_inside_event_loop_and_callback_thread(
    http_server: HTTPState, closed_http: list[aiohttp.ClientTimeout]
) -> None:
    handle = make_handle(http_server, asynchronous=False)
    caller = threading.get_ident()
    seen: list[int] = []

    async def run() -> None:
        await asyncio.sleep(0)
        assert asyncio.get_running_loop().is_running()
        assert isinstance(handle, JobHandle)
        result = handle.wait(on_progress=lambda _: seen.append(threading.get_ident()))
        assert result.status == "succeeded"

    asyncio.run(run())
    assert seen == [caller]
    assert len(closed_http) == 2


def test_bridge_startup_counts_toward_deadline(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    original = threading.Thread.run

    def delayed_start(thread: threading.Thread) -> None:
        if thread.name == "lyra-polling":
            assert release.wait(2)
        original(thread)

    monkeypatch.setattr(threading.Thread, "run", delayed_start)
    handle = make_handle(http_server, asynchronous=False)
    try:
        start = time.monotonic()
        with pytest.raises(JobWaitTimeoutError):
            resolve(handle.wait(timeout=0.1))
        assert time.monotonic() - start < 0.6
        assert len(http_server.calls) == 1
        assert not closed_http
    finally:
        release.set()


def test_sync_interrupt_cancels_active_http(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_server.mode = "body"
    handle = make_handle(http_server, asynchronous=False)

    def interrupt(_self: object, timeout: float | None = None) -> None:
        assert http_server.entered.wait(timeout)
        raise KeyboardInterrupt

    monkeypatch.setattr(concurrent.futures.Future, "result", interrupt)
    with pytest.raises(KeyboardInterrupt):
        resolve(handle.wait(timeout=1))
    assert http_server.finished.wait(1)
    assert [method for method, _ in http_server.calls] == ["POST", "GET"]
    assert closed_http


@pytest.fixture
def dataframe_files(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    paths: list[Path] = []

    @contextmanager
    def temporary() -> Iterator[Path]:
        with dataframe_path() as path:
            paths.append(path)
            yield path

    monkeypatch.setattr("lyra.api.client.async_.dataframe_path", temporary)
    return paths


async def wait_event(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 2)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_dataframe_parsing_worker_owns_file(
    http_server: HTTPState,
    dataframe_files: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    *,
    cancel: bool,
    failure: bool,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []
    background_errors: list[dict[str, Any]] = []
    original = pd.read_json

    def read_json(path: Path, *, lines: bool) -> pd.DataFrame:
        worker_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        assert path.exists()
        if failure:
            message = "parsing failed"
            raise ValueError(message)
        return original(path, lines=lines)

    def load_pandas() -> object:
        worker_threads.append(threading.get_ident())
        return pd

    monkeypatch.setattr(pd, "read_json", read_json)
    monkeypatch.setattr("lyra.api.client.async_.load_pandas", load_pandas)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(
            lambda _loop, context: background_errors.append(context)
        )
        client = AsyncLyraClient(http_server.host, secure=False)
        task = asyncio.create_task(client.results.dataframe("job-1"))
        try:
            await wait_event(entered)
            assert all(worker != threading.get_ident() for worker in worker_threads)
            # Reaching here proves another coroutine progressed during parsing.
            assert dataframe_files[0].exists()
            if cancel:
                start = time.monotonic()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert time.monotonic() - start < 0.5
                assert dataframe_files[0].exists()
            release.set()
            if not cancel:
                if failure:
                    with pytest.raises(ValueError, match="parsing failed"):
                        await task
                else:
                    assert (await task).to_dict("list") == {"value": [6, 7]}
            await loop.shutdown_default_executor()
            await asyncio.sleep(0)
            assert not dataframe_files[0].exists()
        finally:
            release.set()

    asyncio.run(run())
    assert len(worker_threads) == 2
    assert not background_errors
    assert http_server.calls == [("GET", "/jobs/job-1/result/table.jsonl")]


def test_dataframe_download_cancellation_cleans_immediately(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    dataframe_files: list[Path],
) -> None:
    http_server.mode = "download"

    async def run() -> None:
        client = AsyncLyraClient(http_server.host, secure=False)
        task = asyncio.create_task(client.results.dataframe("job-1"))
        await wait_event(http_server.entered)
        assert dataframe_files[0].exists()
        start = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - start < 0.5
        assert not dataframe_files[0].exists()

    asyncio.run(run())
    assert http_server.calls == [("GET", "/jobs/job-1/result/table.jsonl")]
    assert closed_http


def test_dataframe_executor_submission_failure_cleans_file(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    dataframe_files: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor

        def submit(
            executor: concurrent.futures.Executor | None,
            function: Callable[..., object],
            *args: object,
        ) -> asyncio.Future[object]:
            if getattr(function, "__name__", "") == "_read_dataframe":
                message = "executor stopped"
                raise RuntimeError(message)
            return original(executor, function, *args)

        monkeypatch.setattr(loop, "run_in_executor", submit)
        client = AsyncLyraClient(http_server.host, secure=False)
        with pytest.raises(RuntimeError, match="executor stopped"):
            await client.results.dataframe("job-1")
        assert not dataframe_files[0].exists()

    asyncio.run(run())
    assert closed_http


def test_async_dataframe_missing_pandas(monkeypatch: pytest.MonkeyPatch) -> None:
    def import_module(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr("lyra.api.client.base.importlib.import_module", import_module)
    client = AsyncLyraClient("unused.test")
    with pytest.raises(DownloadError, match="pandas is required"):
        asyncio.run(client.results.dataframe("job-1"))


def test_async_dataframe_malformed_jsonl(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    dataframe_files: list[Path],
) -> None:
    http_server.table = b"not json\n"
    client = AsyncLyraClient(http_server.host, secure=False)
    with pytest.raises(ValueError, match="Unexpected character"):
        asyncio.run(client.results.dataframe("job-1"))
    assert not dataframe_files[0].exists()
    assert closed_http


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_polling_does_not_round_long_deadlines(
    http_server: HTTPState,
    closed_http: list[aiohttp.ClientTimeout],
    *,
    asynchronous: bool,
) -> None:
    http_server.mode = "headers"
    handle = make_handle(http_server, asynchronous=asynchronous)
    start = time.monotonic()
    with pytest.raises(JobWaitTimeoutError):
        resolve(handle.wait(timeout=5.01))
    assert 5 <= time.monotonic() - start < 5.51
    assert not http_server.finished.is_set()
    assert closed_http[-1].ceil_threshold == float("inf")


def test_sync_reuses_one_bridge_per_wait(
    http_server: HTTPState, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[threading.Thread] = []
    original = threading.Thread.start

    def start(thread: threading.Thread) -> None:
        if thread.name == "lyra-polling":
            started.append(thread)
        original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    handle = make_handle(http_server, asynchronous=False)
    for _ in range(2):
        assert resolve(handle.wait()).status == "succeeded"
    assert len(started) == 2
    assert len(http_server.calls) == 5


def test_async_wait_cancellation_closes_active_observation(
    http_server: HTTPState,
) -> None:
    http_server.mode = "body"
    handle = make_handle(http_server, asynchronous=True)
    assert isinstance(handle, AsyncJobHandle)

    async def run() -> None:
        task = asyncio.ensure_future(handle.wait())
        await wait_event(http_server.entered)
        start = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - start < 0.5

    asyncio.run(run())
    assert http_server.finished.wait(1)
    assert [method for method, _ in http_server.calls] == ["POST", "GET"]


def test_cancelling_queued_dataframe_parse_preserves_worker_cleanup(
    http_server: HTTPState, dataframe_files: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=1))
        original = loop.run_in_executor
        submitted = asyncio.Event()

        def submit(
            executor: concurrent.futures.Executor | None,
            function: Callable[..., object],
            *args: object,
        ) -> asyncio.Future[object]:
            if getattr(function, "__name__", "") == "_read_dataframe":
                original(None, release.wait, 3)
                submitted.set()
            return original(executor, function, *args)

        monkeypatch.setattr(loop, "run_in_executor", submit)
        task = asyncio.create_task(
            AsyncLyraClient(http_server.host, secure=False).results.dataframe("job-1")
        )
        try:
            async with asyncio.timeout(2):
                await submitted.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert dataframe_files[0].exists()
            release.set()
            await loop.shutdown_default_executor()
            assert not dataframe_files[0].exists()
        finally:
            release.set()

    asyncio.run(run())
