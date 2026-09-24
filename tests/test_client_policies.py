from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypeVar, cast

import aiohttp
import pytest
import requests
from aiohttp.client_reqrep import ConnectionKey
from lyra.api import (
    AsyncJobHandle,
    AsyncLyraAdminClient,
    AsyncLyraClient,
    DownloadError,
    JobHandle,
    JobPollingError,
    JobWaitTimeoutError,
    LyraAdminClient,
    LyraClient,
    MetricRunError,
    ServiceUnavailableError,
    SubmitOptions,
)
from lyra.api.client.results import dataframe_path

from tests.test_api_client_jobs import (
    FakeAsyncFile,
    FakeAsyncResponse,
    FakeContent,
    FakeSession,
    FakeSyncResponse,
    _database_unavailable_response,
    _job_response,
    _readiness_response,
    _result_response,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from pathlib import Path

    from lyra.sdk.models.job import JobProgress
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
        "lyra.api.client.polling.time", SimpleNamespace(monotonic=lambda: scenario.now)
    )
    monkeypatch.setattr(
        "lyra.api.client.polling.random.SystemRandom.uniform",
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
    if status == 200 and resource != "submission":
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


def status_reply(
    status: str = "running", *, current: float | None = None, stamp: int = 0
) -> Reply:
    payload: dict[str, Any] = {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": status,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    if current is not None:
        payload["progress"] = {
            "timestamp": f"2026-01-01T00:00:0{stamp}Z",
            "stage": "work",
            "current": current,
        }
    return Reply(payload=payload)


def submit_handle(scenario: Scenario) -> JobHandle | AsyncJobHandle:
    scenario.replies.insert(0, Reply(status=202, payload=_job_response()))
    return resolve(scenario.client.raw.submit("heavy_metric", {}))


def test_wait_polls_and_compares_progress_content(scenario: Scenario) -> None:
    scenario.replies = [
        status_reply(current=2),
        status_reply(current=2, stamp=1),
        status_reply(current=1, stamp=2),
        status_reply("succeeded"),
        Reply(payload=_result_response()),
    ]
    seen: list[float] = []
    handle = submit_handle(scenario)

    def progress(snapshot: JobProgress) -> None:
        seen.append(snapshot.current)

    assert resolve(handle.wait(on_progress=progress)).status == "succeeded"
    assert seen == [2, 1]
    assert scenario.sleeps == [5.5, 5.5, 5.5]
    assert len(scenario.calls) == 6
    assert all("events" not in url for _, url, _ in scenario.calls)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_wait_retries_transient_status_with_guidance(
    scenario: Scenario, status: int
) -> None:
    scenario.replies = [
        Reply(status=status, text="temporary", headers={"Retry-After": "3"}),
        status_reply("succeeded"),
        Reply(payload=_result_response()),
    ]
    assert resolve(submit_handle(scenario).wait()).status == "succeeded"
    assert scenario.sleeps == [3]


def test_wait_retries_result_and_resets_counter(scenario: Scenario) -> None:
    scenario.replies = [
        *[Reply(status=503)] * 5,
        status_reply("succeeded"),
        *[Reply(status=503)] * 5,
        Reply(payload=_result_response()),
    ]
    assert resolve(submit_handle(scenario).wait()).status == "succeeded"
    assert scenario.sleeps[:5] == scenario.sleeps[5:]


def test_wait_exhausts_five_retries(scenario: Scenario) -> None:
    scenario.replies = [Reply(status=503)] * 6
    with pytest.raises(JobPollingError) as exc:
        resolve(submit_handle(scenario).wait())
    assert exc.value.job_id == "job-1"
    assert exc.value.attempts == 5
    assert len(scenario.sleeps) == 5


@pytest.mark.parametrize("seconds", [0, 2])
def test_wait_deadline_bounds_observations_and_sleep(
    scenario: Scenario, seconds: int
) -> None:
    scenario.replies = [status_reply()]
    with pytest.raises(JobWaitTimeoutError) as exc:
        resolve(submit_handle(scenario).wait(timeout=seconds))
    assert exc.value.job_id == "job-1"
    assert scenario.now == seconds
    assert len(scenario.calls) == (1 if seconds == 0 else 2)
    if seconds:
        options = scenario.calls[-1][2]
        if not scenario.asynchronous:
            assert options["timeout"] == seconds


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_wait_permanent_errors_do_not_retry(scenario: Scenario, status: int) -> None:
    scenario.replies = [Reply(status=status)]
    with pytest.raises(DownloadError):
        resolve(submit_handle(scenario).wait())
    assert scenario.sleeps == []


def test_wait_explicit_nonretryable_service_response(scenario: Scenario) -> None:
    scenario.replies = [
        Reply(
            status=503,
            payload={
                "detail": {"code": "unavailable", "message": "stop", "retryable": False}
            },
        )
    ]
    with pytest.raises(ServiceUnavailableError):
        resolve(submit_handle(scenario).wait())
    assert scenario.sleeps == []


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_wait_raises_terminal_failure(scenario: Scenario, status: str) -> None:
    scenario.replies = [
        status_reply(status),
        Reply(
            payload={
                "job_id": "job-1",
                "kind": status,
                "status": status,
                "error": {"type": "test"},
            }
        ),
    ]
    with pytest.raises(MetricRunError) as exc:
        resolve(submit_handle(scenario).wait())
    assert exc.value.status == status


def test_progress_callback_failure_is_not_retried(scenario: Scenario) -> None:
    error = DownloadError("callback failed", retryable=True)

    def callback(_: JobProgress) -> None:
        raise error

    scenario.replies = [status_reply(current=1)]
    with pytest.raises(DownloadError) as exc:
        resolve(submit_handle(scenario).wait(on_progress=callback))
    assert exc.value is error
    assert scenario.sleeps == []


def test_async_progress_callback_is_awaited(scenario: Scenario) -> None:
    if not scenario.asynchronous:
        return
    seen: list[float] = []

    async def callback(snapshot: JobProgress) -> None:
        await asyncio.sleep(0)
        seen.append(snapshot.current)

    scenario.replies = [
        status_reply("succeeded", current=1),
        Reply(payload=_result_response()),
    ]
    handle = submit_handle(scenario)
    assert isinstance(handle, AsyncJobHandle)
    resolve(handle.wait(on_progress=callback))
    assert seen == [1]


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan")])
def test_poll_interval_must_be_positive_finite(
    scenario: Scenario, interval: float
) -> None:
    with pytest.raises(ValueError, match="poll_interval"):
        resolve(submit_handle(scenario).wait(poll_interval=interval))


def test_wait_transport_timeout_retry_and_certificate_failure(
    scenario: Scenario,
) -> None:
    transient = TimeoutError() if scenario.asynchronous else requests.Timeout()
    scenario.replies = [
        transient,
        status_reply("succeeded"),
        Reply(payload=_result_response()),
    ]
    assert resolve(submit_handle(scenario).wait()).status == "succeeded"
    assert len(scenario.sleeps) == 1
    permanent = (
        aiohttp.ClientSSLError(
            ConnectionKey(
                host="test",
                port=443,
                is_ssl=True,
                ssl=True,
                proxy=None,
                proxy_auth=None,
                proxy_headers_hash=None,
            ),
            OSError("certificate"),
        )
        if scenario.asynchronous
        else requests.exceptions.SSLError("certificate")
    )
    scenario.replies = [permanent]
    with pytest.raises(DownloadError):
        resolve(submit_handle(scenario).wait())
    assert len(scenario.sleeps) == 1


def test_async_cancelling_wait_does_not_cancel_remote_job(
    scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not scenario.asynchronous:
        return
    scenario.replies = [status_reply()]
    handle = submit_handle(scenario)
    assert isinstance(handle, AsyncJobHandle)
    entered = asyncio.Event()

    async def sleep(_: float) -> None:
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr("lyra.api.client.async_.asyncio", SimpleNamespace(sleep=sleep))

    async def run() -> None:
        task = asyncio.ensure_future(handle.wait())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert [method for method, _, _ in scenario.calls] == ["POST", "GET"]


def test_terminal_result_retries_are_bounded_by_deadline(scenario: Scenario) -> None:
    scenario.replies = [
        status_reply("succeeded"),
        Reply(status=503, headers={"Retry-After": "60"}),
    ]
    with pytest.raises(JobWaitTimeoutError):
        resolve(submit_handle(scenario).wait(timeout=2))
    assert scenario.now == 2
    assert scenario.sleeps == [2]


def test_malformed_polling_payload_is_permanent(scenario: Scenario) -> None:
    scenario.replies = [Reply(text="not json")]
    with pytest.raises(DownloadError):
        resolve(submit_handle(scenario).wait())
    assert not scenario.sleeps
