import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from lyra.sdk.models import JobCreateRequest, JobResult

from lyra_app import job_store, registry
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from lyra_app.routes import jobs


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [
            {
                "name": "heavy_metric",
                "description": "A heavy metric.",
                "request_schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer"}},
                },
                "execution": {"queue": "priority-lane"},
                "entrypoint": "fake_plugin.runner:run",
            }
        ],
    }


def _synced_repo(repo: Path) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        owner="owner",
        repo="repo",
        ref=None,
    )
    return SyncedPluginRepo(entry=entry, path=repo, changed=False)


class FakeRedisAsync:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.deleted: list[str] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}

    async def ping(self) -> bool:
        return self.available

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations.append((key, ex))

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    async def xadd(self, key: str, fields: dict[str, str]) -> str:
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, fields))
        return stream_id

    async def xrange(
        self,
        key: str,
        *,
        min: str,  # noqa: A002
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        records = self.streams.get(key, [])
        if min.startswith("("):
            after_id = min[1:]
            records = [record for record in records if record[0] > after_id]
        elif min != job_store.STREAM_START:
            records = [record for record in records if record[0] >= min]
        return records if count is None else records[:count]

    async def xread(
        self,
        streams: dict[str, str],
        *,
        block: int,  # noqa: ARG002
        count: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        key, after_id = next(iter(streams.items()))
        records = self.streams.get(key, [])
        if after_id != job_store.STREAM_LATEST:
            records = [record for record in records if record[0] > after_id]
        else:
            records = []
        if count is not None:
            records = records[:count]
        return [(key, records)] if records else []


class FakeCelery:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_task(
        self,
        name: str,
        *,
        args: list[dict[str, Any]],
        queue: str,
        task_id: str,
    ) -> None:
        self.sent.append(
            {"name": name, "args": args, "queue": queue, "task_id": task_id}
        )


class FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def reset_catalog() -> None:
    registry.reset_catalog()


def _use_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])
    registry.refresh_catalog()


def _patch_redis(monkeypatch: pytest.MonkeyPatch, redis: FakeRedisAsync) -> None:
    monkeypatch.setattr(jobs, "redis_client", redis)
    monkeypatch.setattr(jobs.job_store, "redis_client", redis)


async def _body(response: StreamingResponse) -> str:
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        async for chunk in response.body_iterator
    ]
    return "".join(chunks)


def test_create_job_dispatches_generic_task_to_manifest_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    monkeypatch.setattr(jobs, "celery_app", celery)
    monkeypatch.setattr(jobs, "uuid4", lambda: SimpleNamespace(hex="job-1"))

    response = asyncio.run(
        jobs.create_job(
            JobCreateRequest(
                metric="heavy_metric",
                input={"value": 3},
                idempotency_key="key-1",
            )
        )
    )

    assert response.model_dump() == {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "queued",
        "links": {
            "self": "/jobs/job-1",
            "events": "/jobs/job-1/events",
            "result": "/jobs/job-1/result",
        },
    }
    assert celery.sent == [
        {
            "name": "lyra.run_metric",
            "args": [
                {
                    "job_id": "job-1",
                    "metric": "heavy_metric",
                    "input": {"value": 3},
                    "idempotency_key": "key-1",
                    "metadata": {},
                }
            ],
            "queue": "priority-lane",
            "task_id": "job-1",
        }
    ]
    assert json.loads(redis.values[job_store.status_key("job-1")])["status"] == (
        "queued"
    )
    assert len(redis.streams[job_store.events_key("job-1")]) == 1


def test_create_job_rejects_unknown_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(JobCreateRequest(metric="missing", input={"value": 3}))
        )

    assert exc_info.value.status_code == 404


def test_create_job_rejects_invalid_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.create_job(JobCreateRequest(metric="heavy_metric", input={})))

    assert exc_info.value.status_code == 422


def test_create_job_returns_503_when_redis_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    _patch_redis(monkeypatch, FakeRedisAsync(available=False))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(JobCreateRequest(metric="heavy_metric", input={"value": 3}))
        )

    assert exc_info.value.status_code == 503


def test_get_job_returns_current_status(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(
        job_store.set_job_status_async("job-1", "started", metric="heavy_metric")
    )

    response = asyncio.run(jobs.get_job("job-1"))

    assert response.job_id == "job-1"
    assert response.metric == "heavy_metric"
    assert response.status == "started"


def test_get_job_returns_404_for_missing_job(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.get_job("missing"))

    assert exc_info.value.status_code == 404


def test_job_events_stream_typed_sse_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(job_store.set_job_status_async("job-1", "queued", metric="metric"))
    first_id = redis.streams[job_store.events_key("job-1")][0][0]
    asyncio.run(
        job_store.set_job_status_async(
            "job-1",
            "succeeded",
            event_data={"job_id": "job-1", "status": "succeeded"},
        )
    )

    response = asyncio.run(
        jobs.get_job_events(
            "job-1",
            cast("Request", FakeRequest()),
            last_event_id=first_id,
        )
    )
    body = asyncio.run(_body(response))

    assert "id: 2-0\n" in body
    assert "event: succeeded\n" in body
    assert 'data: {"job_id":"job-1","event":"succeeded"' in body
    assert "event: queued\n" not in body


def test_job_events_stream_closes_after_terminal_last_event_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(
        job_store.set_job_status_async(
            "job-1",
            "succeeded",
            event_data={"job_id": "job-1", "status": "succeeded"},
        )
    )
    terminal_id = redis.streams[job_store.events_key("job-1")][0][0]

    response = asyncio.run(
        jobs.get_job_events(
            "job-1",
            cast("Request", FakeRequest()),
            last_event_id=terminal_id,
        )
    )

    assert asyncio.run(_body(response)) == ""


def test_job_result_returns_404_before_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.get_job_result("job-1", BackgroundTasks()))

    assert exc_info.value.status_code == 404


def test_job_result_returns_json_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    redis.values[job_store.result_key("job-1")] = json.dumps(
        JobResult(job_id="job-1", status="failed", error={"type": "worker"}).model_dump(
            mode="json",
            exclude_none=True,
        )
    )

    response = asyncio.run(jobs.get_job_result("job-1", BackgroundTasks()))

    assert isinstance(response, JSONResponse)
    assert json.loads(bytes(response.body)) == {
        "job_id": "job-1",
        "status": "failed",
        "error": {"type": "worker"},
    }


def test_job_result_returns_file_and_cleans_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    output = tmp_path / "result.tif"
    output.write_bytes(b"data")
    redis.values[job_store.result_key("job-1")] = json.dumps(
        JobResult(
            job_id="job-1",
            status="succeeded",
            result_type="file",
            file_path=str(output),
        ).model_dump(mode="json", exclude_none=True)
    )
    background_tasks = BackgroundTasks()

    response = asyncio.run(jobs.get_job_result("job-1", background_tasks))
    asyncio.run(background_tasks())

    assert isinstance(response, FileResponse)
    assert not output.exists()
    assert redis.deleted == [job_store.result_key("job-1")]
