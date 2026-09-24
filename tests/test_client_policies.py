from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypeVar, cast

import aiohttp
import pytest
import requests
from lyra.api import (
    AsyncLyraAdminClient,
    AsyncLyraClient,
    DownloadError,
    JobEventCursorGapError,
    JobEventStreamError,
    JobWaitTimeoutError,
    LyraAdminClient,
    LyraClient,
    MetricRunError,
    ServiceUnavailableError,
    SubmitOptions,
)
from lyra.api.client.events import StreamState
from lyra.api.client.results import dataframe_path

from tests.test_api_client_jobs import (
    FakeAsyncFile,
    FakeAsyncResponse,
    FakeContent,
    FakeSession,
    FakeSyncResponse,
    _database_unavailable_response,
    _job_response,
    _progress_event_lines,
    _readiness_response,
    _result_response,
    _terminal_event_lines,
)

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.sdk.models.job import JobEventRecord
    from lyra.sdk.types import JsonValue

_T = TypeVar("_T")


def resolve(value: _T | Awaitable[_T]) -> _T:
    if inspect.isawaitable(value):

        async def run() -> _T:
            return cast("_T", await value)

        return asyncio.run(run())
    return cast("_T", value)


@dataclass
class Reply:
    status: int = 200
    payload: JsonValue = None
    text: str | None = None
    lines: list[str] = field(default_factory=list)
    chunks: list[bytes] = field(default_factory=list)
    headers: dict[str, str] = field(
        default_factory=lambda: {"content-type": "application/json"}
    )
    read_error: BaseException | None = None
    on_line: Callable[[], None] = lambda: None
    closed: bool = False


class SyncReply(FakeSyncResponse):
    def __init__(self, reply: Reply) -> None:
        super().__init__(
            status_code=reply.status,
            payload=reply.payload,
            headers=reply.headers,
            lines=reply.lines,
            chunks=reply.chunks,
        )
        self.reply = reply
        if reply.text is not None:
            self.text = reply.text

    def __exit__(self, *args: object) -> None:
        self.reply.closed = True

    def iter_lines(self, *, decode_unicode: bool) -> Iterator[str]:
        for line in super().iter_lines(decode_unicode=decode_unicode):
            self.reply.on_line()
            yield line
        if self.reply.read_error is not None:
            raise self.reply.read_error


class AsyncContent(FakeContent):
    def __init__(self, reply: Reply) -> None:
        super().__init__(lines=reply.lines, chunks=reply.chunks)
        self.reply = reply

    async def _iter_lines(self) -> AsyncIterator[bytes]:
        async for line in super()._iter_lines():
            self.reply.on_line()
            yield line
        if self.reply.read_error is not None:
            raise self.reply.read_error


class AsyncReply(FakeAsyncResponse):
    def __init__(self, reply: Reply) -> None:
        super().__init__(
            status=reply.status, payload=reply.payload, headers=reply.headers
        )
        self.reply = reply
        self.content = AsyncContent(reply)

    async def text(self) -> str:
        return self.reply.text if self.reply.text is not None else await super().text()

    async def __aexit__(self, *args: object) -> None:
        self.reply.closed = True


@dataclass
class Scenario:
    asynchronous: bool
    replies: list[Reply | BaseException] = field(default_factory=list)
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    now: float = 0.0

    @property
    def client(self) -> LyraClient | AsyncLyraClient:
        cls = AsyncLyraClient if self.asynchronous else LyraClient
        return cls("example.test", agent_api_key="consumer")

    @property
    def admin(self) -> LyraAdminClient | AsyncLyraAdminClient:
        cls = AsyncLyraAdminClient if self.asynchronous else LyraAdminClient
        return cls("example.test", admin_api_key="admin")

    def request(self, method: str, url: str, **kwargs: object) -> Reply:
        self.calls.append((method, url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def collect(
        self,
        *,
        after_id: str | None = None,
        kinds: set[str] | None = None,
        timeout: float | None = None,
        max_reconnect_attempts: int = 5,
    ) -> list[JobEventRecord]:
        stream = self.client.jobs.events(
            "job-1",
            after_id=after_id,
            kinds=kinds,
            timeout=timeout,
            max_reconnect_attempts=max_reconnect_attempts,
        )
        if isinstance(stream, AsyncIterator):

            async def collect() -> list[JobEventRecord]:
                return [event async for event in stream]

            return asyncio.run(collect())
        return list(stream)

    def advance(self, seconds: float = 1.0) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def scenario(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Scenario:
    scenario = Scenario(asynchronous=request.param)

    def sync_request(method: str, url: str, **kwargs: object) -> SyncReply:
        return SyncReply(scenario.request(method, url, **kwargs))

    def sync_get(url: str, **kwargs: object) -> SyncReply:
        return sync_request("GET", url, **kwargs)

    class Session(FakeSession):
        def request(self, *args: object, **kwargs: object) -> AsyncReply:
            assert isinstance(self, Session)
            return AsyncReply(scenario.request(str(args[0]), str(args[1]), **kwargs))

        def get(self, *args: object, **kwargs: object) -> AsyncReply:
            return self.request("GET", *args, **kwargs)

    async def sleep(seconds: float) -> None:
        scenario.sleep(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(requests, "request", sync_request)
    monkeypatch.setattr(requests, "get", sync_get)
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(
        "lyra.api.client.events.time", SimpleNamespace(monotonic=lambda: scenario.now)
    )
    monkeypatch.setattr(
        "lyra.api.client.events.random.SystemRandom.uniform",
        lambda _self, _low, high: high,
    )
    monkeypatch.setattr(
        "lyra.api.client.sync.time", SimpleNamespace(sleep=scenario.sleep)
    )
    monkeypatch.setattr("lyra.api.client.async_.asyncio", SimpleNamespace(sleep=sleep))
    return scenario


@pytest.mark.parametrize(
    ("status", "text"),
    [(200, "not json"), (200, "{}"), (503, "oops"), (503, "{}"), (401, "denied")],
)
@pytest.mark.parametrize("resource", ["health", "submission", "admin", "result"])
def test_request_failures_have_context_and_close(
    scenario: Scenario, status: int, text: str, resource: str
) -> None:
    reply = Reply(status=status, text=text)
    scenario.replies = [reply]
    operations = {
        "health": scenario.client.health.liveness,
        "submission": lambda: scenario.client.raw.create("metric", {}),
        "admin": scenario.admin.status,
        "result": lambda: scenario.client.results.get("job-1"),
    }
    with pytest.raises(DownloadError, match="Failed to") as error:
        resolve(operations[resource]())
    if (status == 200 and resource != "submission") or (
        status == 503 and text == "oops"
    ):
        assert error.value.__cause__ is not None
    assert reply.closed
    assert len(scenario.calls) == 1


@pytest.mark.parametrize(
    ("retry_after", "expected"), [("4", 4), ("invalid", None), (None, None)]
)
@pytest.mark.parametrize("resource", ["health", "submission", "admin", "download"])
def test_structured_unavailability(
    scenario: Scenario,
    tmp_path: Path,
    retry_after: str | None,
    expected: int | None,
    resource: str,
) -> None:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    scenario.replies = [
        Reply(status=503, payload=_database_unavailable_response(), headers=headers)
    ]
    operations = {
        "health": scenario.client.health.liveness,
        "submission": lambda: scenario.client.raw.create("metric", {}),
        "admin": scenario.admin.status,
        "download": lambda: scenario.client.results.download(
            "job-1", tmp_path / "result"
        ),
    }
    with pytest.raises(ServiceUnavailableError) as error:
        resolve(operations[resource]())
    assert error.value.retry_after_seconds == expected
    assert error.value.code == "database_unavailable"
    assert error.value.retryable
    assert len(scenario.calls) == 1


def test_readiness_503_and_submission_contract(scenario: Scenario) -> None:
    payload = _readiness_response() | {"status": "not_ready"}
    scenario.replies = [
        Reply(status=503, payload=payload),
        Reply(status=202, payload=_job_response()),
    ]
    assert resolve(scenario.client.health.readiness()).status == "not_ready"
    resolve(
        scenario.client.raw.create(
            "metric", {"value": 3}, options=SubmitOptions(idempotency_key="key")
        )
    )
    assert scenario.calls[0][2]["headers"] == {}
    assert scenario.calls[1][:2] == ("POST", "https://example.test/jobs")
    assert scenario.calls[1][2]["headers"] == {"Authorization": "Bearer consumer"}
    assert scenario.calls[1][2]["json"] == {
        "metric": "metric",
        "input": {"value": 3},
        "idempotency_key": "key",
    }


def test_transport_timeout_is_chained_without_retry(scenario: Scenario) -> None:
    cause = TimeoutError("late") if scenario.asynchronous else requests.Timeout("late")
    scenario.replies = [cause]
    with pytest.raises(DownloadError, match="create job") as error:
        resolve(scenario.client.raw.create("metric", {}))
    assert error.value.__cause__ is cause
    assert len(scenario.calls) == 1


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_handle_and_raw_results(scenario: Scenario, status: str) -> None:
    payload: dict[str, Any] = {
        "job_id": "job-1",
        "status": status,
        "kind": status,
        "error": {"message": "stopped"},
    }
    scenario.replies = [
        Reply(status=202, payload=_job_response()),
        Reply(payload=payload),
        Reply(payload=payload),
    ]
    handle = resolve(scenario.client.raw.submit("metric", {}))
    with pytest.raises(MetricRunError) as error:
        resolve(handle.result())
    assert error.value.status == status
    assert resolve(scenario.client.results.get("job-1")).status == status


@pytest.mark.parametrize("text", [None, "{", "{}"])
def test_file_json_rejection_preserves_destination(
    scenario: Scenario, tmp_path: Path, text: str | None
) -> None:
    reply = Reply(payload=_result_response(), text=text)
    scenario.replies = [reply]
    path = tmp_path / "result"
    path.write_bytes(b"original")
    with pytest.raises(DownloadError):
        resolve(scenario.client.results.download_file("job-1", path))
    assert path.read_bytes() == b"original"
    assert reply.closed


@pytest.mark.parametrize("failure", [False, True])
def test_dataframe_temp_cleanup(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, *, failure: bool
) -> None:
    paths: list[Path] = []

    class Pandas:
        @staticmethod
        def read_json(path: Path, *, lines: bool) -> object:
            assert lines
            paths.append(path)
            assert path.read_bytes() == b'{"value":6}\n'
            if failure:
                message = "hydration failed"
                raise ValueError(message)
            return "frame"

    monkeypatch.setattr("lyra.api.client.sync.load_pandas", lambda: Pandas)
    monkeypatch.setattr("lyra.api.client.async_.load_pandas", lambda: Pandas)
    monkeypatch.setattr(
        "lyra.api.client.async_.aiofiles.open", lambda path, _mode: FakeAsyncFile(path)
    )
    scenario.replies = [Reply(chunks=[b'{"value":6}\n'])]
    if failure:
        with pytest.raises(ValueError, match="hydration failed"):
            resolve(scenario.client.results.dataframe("job-1"))
    else:
        assert resolve(scenario.client.results.dataframe("job-1")) == "frame"
    assert len(paths) == 1
    assert not paths[0].exists()


@pytest.mark.parametrize(
    "lines", [["data: {}", ""], ["id: 1", "data: {", ""], ["id: 1", "data: {}", ""]]
)
def test_malformed_events_fail_without_retry(
    scenario: Scenario, lines: list[str]
) -> None:
    reply = Reply(lines=lines)
    scenario.replies = [reply]
    with pytest.raises(DownloadError):
        scenario.collect()
    assert reply.closed
    assert len(scenario.calls) == 1
    assert not scenario.sleeps


def test_multiline_eof_event_and_comment(scenario: Scenario) -> None:
    lines = _terminal_event_lines()
    data = next(line for line in lines if line.startswith("data: "))
    split = data.index(",") + 1
    scenario.replies = [
        Reply(lines=[": heartbeat", lines[0], data[:split], "data: " + data[split:]])
    ]
    assert [record.event.name for record in scenario.collect()] == ["succeeded"]


def test_resume_duplicate_filter_and_retry_reset(scenario: Scenario) -> None:
    disconnect = (
        aiohttp.ClientConnectionError("lost")
        if scenario.asynchronous
        else requests.ConnectionError("lost")
    )
    scenario.replies = [
        Reply(status=500),
        Reply(lines=_progress_event_lines(), read_error=disconnect),
        Reply(lines=_progress_event_lines() + _terminal_event_lines("2-0")),
    ]
    assert scenario.collect(kinds={"message"}, max_reconnect_attempts=1) == []
    assert scenario.sleeps == [0.5, 0.5]
    assert scenario.calls[2][2]["headers"]["Last-Event-ID"] == "1-0"
    assert len(scenario.calls) == 3


def test_replayed_event_is_not_yielded(scenario: Scenario) -> None:
    scenario.replies = [
        Reply(lines=_progress_event_lines() + _terminal_event_lines("2-0"))
    ]
    assert [record.id for record in scenario.collect(after_id="1-0")] == ["2-0"]


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(409, JobEventCursorGapError), (403, DownloadError), (503, JobEventStreamError)],
)
def test_stream_status_policy(
    scenario: Scenario, status: int, error_type: type[DownloadError]
) -> None:
    reply = Reply(status=status)
    scenario.replies = [reply]
    with pytest.raises(error_type) as error:
        scenario.collect(after_id="old", max_reconnect_attempts=0)
    if isinstance(error.value, JobEventStreamError):
        assert error.value.last_event_id == "old"
        assert error.value.job_id == "job-1"
    assert reply.closed
    assert not scenario.sleeps


def test_retry_exhaustion_and_jitter_cap(scenario: Scenario) -> None:
    scenario.replies = [Reply() for _ in range(7)]
    with pytest.raises(JobEventStreamError) as error:
        scenario.collect(max_reconnect_attempts=6)
    assert error.value.attempts == 7
    assert scenario.sleeps == [0.5, 1, 2, 4, 8, 8]


def test_heartbeat_deadline(scenario: Scenario) -> None:
    reply = Reply(lines=[": heartbeat"] * 4, on_line=scenario.advance)
    scenario.replies = [reply]
    with pytest.raises(JobWaitTimeoutError):
        scenario.collect(timeout=2)
    assert reply.closed
    assert not scenario.sleeps


def test_backoff_deadline(scenario: Scenario) -> None:
    scenario.replies = [Reply(status=500)]
    with pytest.raises(JobWaitTimeoutError):
        scenario.collect(timeout=0.25)
    assert scenario.sleeps == [0.25]
    assert len(scenario.calls) == 1


def test_stream_state_read_timeout_and_negative_limit() -> None:
    state = StreamState("job", timeout=2, clock=lambda: 10)
    assert state.read_timeout(30) == 2
    assert state.read_timeout(1) == 1
    with pytest.raises(ValueError, match="non-negative"):
        StreamState("job", max_reconnect_attempts=-1)


@pytest.mark.parametrize("scenario", [True], indirect=True)
def test_async_callbacks_are_awaited_in_event_order(scenario: Scenario) -> None:
    observed: list[str] = []
    scenario.replies = [
        Reply(status=202, payload=_job_response()),
        Reply(lines=_progress_event_lines() + _terminal_event_lines("2-0")),
        Reply(payload=_result_response()),
    ]

    async def on_event(record: JobEventRecord) -> None:
        await asyncio.sleep(0)
        observed.append(record.id)

    async def run() -> None:
        client = scenario.client
        assert isinstance(client, AsyncLyraClient)
        handle = await client.raw.submit("metric", {})
        result = await handle.wait(on_event=on_event)
        assert result.status == "succeeded"
        assert observed == ["1-0", "2-0"]

    asyncio.run(run())


def test_callback_exceptions_propagate_unchanged(scenario: Scenario) -> None:
    cause = ValueError("callback failed")
    scenario.replies = [
        Reply(status=202, payload=_job_response()),
        Reply(lines=_progress_event_lines()),
    ]

    def on_event(_record: JobEventRecord) -> None:
        raise cause

    handle = resolve(scenario.client.raw.submit("metric", {}))
    with pytest.raises(ValueError, match="callback failed") as error:
        resolve(handle.wait(on_event=on_event))
    assert error.value is cause
    assert not scenario.sleeps


def test_async_cancellation_closes_live_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = Reply()

    async def run() -> None:
        started = asyncio.Event()
        blocked = asyncio.Event()

        class BlockingContent(FakeContent):
            async def _iter_lines(self) -> AsyncIterator[bytes]:
                assert isinstance(self, BlockingContent)
                started.set()
                await blocked.wait()
                yield b": heartbeat"

        response = AsyncReply(reply)
        response.content = BlockingContent()
        FakeSession.responses = [response]
        monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
        stream = AsyncLyraClient("example.test").jobs.events("job-1")

        async def receive() -> JobEventRecord:
            return await anext(stream)

        task = asyncio.create_task(receive())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reply.closed
        assert FakeSession.responses == []

    asyncio.run(run())


def test_stream_read_timeouts_reconnect(scenario: Scenario) -> None:
    cause = TimeoutError("late") if scenario.asynchronous else requests.Timeout("late")
    first = Reply(read_error=cause)
    scenario.replies = [first, Reply(lines=_terminal_event_lines())]
    assert len(scenario.collect()) == 1
    assert first.closed
    assert scenario.sleeps == [0.5]


@pytest.mark.parametrize("resource", ["file", "jsonl"])
def test_streaming_download_and_cleanup(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, resource: str
) -> None:
    reply = Reply(
        headers={"content-type": "application/octet-stream"}, chunks=[b"one", b"two"]
    )
    scenario.replies = [reply]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiofiles.open", lambda path, _mode: FakeAsyncFile(path)
    )
    path = tmp_path / "result"
    if resource == "file":
        resolve(scenario.client.results.download_file("job-1", path))
    else:
        resolve(scenario.client.results.download("lyra://results/job-1", path))
    assert path.read_bytes() == b"onetwo"
    assert reply.closed
    assert scenario.calls[0][2]["headers"] == {"Authorization": "Bearer consumer"}


def test_dataframe_download_failure_cleans_temp_file(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths: list[Path] = []

    @contextmanager
    def temporary() -> Iterator[Path]:
        with dataframe_path() as path:
            paths.append(path)
            yield path

    monkeypatch.setattr("lyra.api.client.sync.dataframe_path", temporary)
    monkeypatch.setattr("lyra.api.client.async_.dataframe_path", temporary)
    monkeypatch.setattr("lyra.api.client.sync.load_pandas", object)
    monkeypatch.setattr("lyra.api.client.async_.load_pandas", object)
    scenario.replies = [Reply(status=404, text="missing")]
    with pytest.raises(DownloadError, match="download result"):
        resolve(scenario.client.results.dataframe("job-1"))
    assert len(paths) == 1
    assert not paths[0].exists()


def test_non_json_raw_result_is_rejected(scenario: Scenario) -> None:
    scenario.replies = [
        Reply(headers={"content-type": "application/octet-stream"}, text="bytes")
    ]
    with pytest.raises(DownloadError, match="response was not JSON"):
        resolve(scenario.client.results.get("job-1"))


@pytest.mark.parametrize("operation", ["request", "download"])
def test_async_invalid_response_encoding_is_chained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str
) -> None:
    cause = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    reply = Reply()

    class InvalidEncodingResponse(AsyncReply):
        async def text(self) -> str:
            await super().text()
            raise cause

    FakeSession.responses = [InvalidEncodingResponse(reply)]
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    client = AsyncLyraClient("example.test")
    call = (
        client.health.liveness()
        if operation == "request"
        else client.results.download_file("job-1", tmp_path / "result")
    )
    with pytest.raises(DownloadError) as error:
        asyncio.run(call)
    assert error.value.__cause__ is cause
    assert reply.closed
