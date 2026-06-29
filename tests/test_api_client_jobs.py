import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest
from lyra.api.client.async_ import AsyncLyraAPIClient
from lyra.api.client.sync import LyraAPIClient
from lyra.api.exceptions import DownloadError


class FakeSyncResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        lines: list[str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {"content-type": "application/json"}
        self._lines = lines or []
        self._chunks = chunks or []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def json(self) -> dict[str, Any]:
        assert self._payload is not None
        return self._payload

    def iter_lines(self, *, decode_unicode: bool) -> Iterator[str]:  # noqa: ARG002
        yield from self._lines

    def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:  # noqa: ARG002
        yield from self._chunks


def _job_response() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "queued",
        "links": {
            "self": "/jobs/job-1",
            "events": "/jobs/job-1/events",
            "result": "/jobs/job-1/result",
        },
    }


def _status_response() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "started",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _terminal_event_lines() -> list[str]:
    event = {
        "job_id": "job-1",
        "event": "succeeded",
        "timestamp": "2026-01-01T00:00:00Z",
        "data": {
            "kind": "table",
            "job_id": "job-1",
            "status": "succeeded",
            "index": ["area-1"],
            "columns": ["value"],
            "data": [[6]],
        },
    }
    return [
        "id: 1-0",
        "event: succeeded",
        f"data: {json.dumps(event)}",
        "",
    ]


def _result_response() -> dict[str, Any]:
    return {
        "kind": "table",
        "job_id": "job-1",
        "status": "succeeded",
        "index": ["area-1"],
        "columns": ["value"],
        "data": [[6]],
    }


def _data_types_response() -> dict[str, Any]:
    return {
        "location": [
            {
                "data_type": "geojson",
                "description": "GeoJSON locations.",
                "wrapper_schema": {"type": "object"},
            }
        ],
        "bounds": [
            {
                "data_type": "geojson",
                "description": "One GeoJSON bounds geometry.",
                "wrapper_schema": {"type": "object"},
            }
        ],
    }


def test_sync_client_uses_job_api_for_job_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict[str, Any]] = []

    def post(
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        posted.append(
            {"url": url, "json": json, "timeout": timeout, "headers": headers}
        )
        return FakeSyncResponse(status_code=202, payload=_job_response())

    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
        stream: bool = False,  # noqa: ARG001
    ) -> FakeSyncResponse:
        if url.endswith("/events"):
            return FakeSyncResponse(lines=_terminal_event_lines())
        if url.endswith("/result"):
            return FakeSyncResponse(payload=_result_response())
        return FakeSyncResponse(payload=_status_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.post", post)
    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    client = LyraAPIClient("example.test", secure=False, timeout=12.0)

    job = client.create_job("heavy_metric", {"value": 3}, idempotency_key="key-1")
    status = client.get_job(job.job_id)
    events = list(client.iter_job_events(job.job_id))
    result = client.get_job_result(job.job_id)
    processed = client.process("heavy_metric", {"value": 3})

    assert posted[0]["url"] == "http://example.test/jobs"
    assert posted[0]["json"] == {
        "metric": "heavy_metric",
        "input": {"value": 3},
        "idempotency_key": "key-1",
    }
    assert job.job_id == "job-1"
    assert status.status == "started"
    assert [event.event for event in events] == ["succeeded"]
    assert result.kind == "table"
    assert result.data == [[6]]
    assert processed.data == [[6]]


def test_sync_client_returns_grouped_data_type_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
    ) -> FakeSyncResponse:
        assert url == "http://example.test/data_types"
        return FakeSyncResponse(payload=_data_types_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    response = LyraAPIClient("example.test", secure=False).get_data_types()

    assert response.location[0].data_type == "geojson"
    assert response.bounds[0].wrapper_schema == {"type": "object"}


def test_sync_client_rejects_invalid_data_type_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,  # noqa: ARG001
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
    ) -> FakeSyncResponse:
        return FakeSyncResponse(payload={"location": []})

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)

    with pytest.raises(DownloadError, match="Invalid data types response format"):
        LyraAPIClient("example.test", secure=False).get_data_types()


def test_sync_client_downloads_file_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,  # noqa: ARG001
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
        stream: bool,
    ) -> FakeSyncResponse:
        assert stream is True
        return FakeSyncResponse(
            headers={"content-type": "image/tiff"},
            chunks=[b"abc", b"def"],
        )

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    output = tmp_path / "result.tif"

    LyraAPIClient("example.test", secure=False).download_job_result_to_file(
        "job-1",
        output,
    )

    assert output.read_bytes() == b"abcdef"


class FakeContent:
    def __init__(
        self,
        *,
        lines: list[str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.lines = lines or []
        self.chunks = chunks or []

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iter_lines()

    async def _iter_lines(self) -> AsyncIterator[bytes]:
        for line in self.lines:
            yield f"{line}\n".encode()

    async def iter_chunked(
        self,
        chunk_size: int,  # noqa: ARG002
    ) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class FakeAsyncResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: dict[str, Any] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        lines: list[str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self._payload = payload
        self._text = text
        self.headers = headers or {"content-type": "application/json"}
        self.content = FakeContent(lines=lines, chunks=chunks)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        assert self._payload is not None
        return self._payload

    async def text(self) -> str:
        return self._text


class FakeSession:
    responses: ClassVar[list[FakeAsyncResponse]] = []

    def __init__(self, **_: object) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def post(self, *_: object, **__: object) -> FakeAsyncResponse:
        return self.responses.pop(0)

    def get(self, *_: object, **__: object) -> FakeAsyncResponse:
        return self.responses.pop(0)


def test_async_client_processes_json_job(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(status=202, payload=_job_response()),
        FakeAsyncResponse(lines=_terminal_event_lines()),
        FakeAsyncResponse(payload=_result_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    result = asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).process(
            "heavy_metric",
            {"value": 3},
        )
    )

    assert result.kind == "table"
    assert result.data == [[6]]


def test_async_client_returns_grouped_data_type_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_data_types_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    response = asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).get_data_types()
    )

    assert response.location[0].data_type == "geojson"
    assert response.bounds[0].wrapper_schema == {"type": "object"}


def test_async_client_rejects_invalid_data_type_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload={"location": []}),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    with pytest.raises(DownloadError, match="Invalid data types response format"):
        asyncio.run(AsyncLyraAPIClient("example.test", secure=False).get_data_types())


def test_async_client_downloads_file_job_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(headers={"content-type": "image/tiff"}, chunks=[b"abc"]),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)
    output = tmp_path / "result.tif"

    asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).download_job_result_to_file(
            "job-1",
            output,
        )
    )

    assert output.read_bytes() == b"abc"
