import asyncio
import json
from typing import Any

import pytest
from lyra.sdk.models import JobEnvelope, JobResult

from lyra_app import job_store


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations.append((key, ex))

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    def xadd(self, key: str, fields: dict[str, str]) -> str:
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, fields))
        return stream_id

    def xrange(
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


class FakeRedisAsync:
    def __init__(self, payload: str | None = None) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.deleted: list[str] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        if payload is not None:
            self.values[job_store.result_key("job-1")] = payload

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


def _load_status(redis: FakeRedisSync, job_id: str) -> dict[str, Any]:
    return json.loads(redis.values[job_store.status_key(job_id)])


def test_create_job_writes_queued_status_and_ttl() -> None:
    redis = FakeRedisSync()
    job = JobEnvelope(job_id="job-1", metric="heavy_metric", input={"value": 1})

    snapshot = job_store.create_job(job, client=redis)

    assert snapshot.status == "queued"
    assert snapshot.metric == "heavy_metric"
    assert _load_status(redis, "job-1")["status"] == "queued"
    events = job_store.read_job_events("job-1", client=redis)
    assert [event.event.event for event in events] == ["queued"]
    assert (job_store.status_key("job-1"), job_store.JOB_STORE_TTL_SECONDS) in (
        redis.expirations
    )
    assert (job_store.result_key("job-1"), job_store.JOB_STORE_TTL_SECONDS) in (
        redis.expirations
    )
    assert (job_store.events_key("job-1"), job_store.JOB_STORE_TTL_SECONDS) in (
        redis.expirations
    )


def test_status_result_and_structured_failure_are_persisted() -> None:
    redis = FakeRedisSync()

    job_store.set_job_status(
        "job-1",
        "started",
        metric="heavy_metric",
        client=redis,
    )
    result = JobResult(
        job_id="job-1",
        status="failed",
        error={"type": "worker", "message": "boom"},
    )
    payload = job_store.save_job_result(
        result,
        metric="heavy_metric",
        client=redis,
    )

    assert payload == {
        "job_id": "job-1",
        "status": "failed",
        "error": {"type": "worker", "message": "boom"},
    }
    assert json.loads(redis.values[job_store.result_key("job-1")]) == payload
    assert _load_status(redis, "job-1")["status"] == "failed"
    assert _load_status(redis, "job-1")["error"] == payload["error"]
    events = job_store.read_job_events("job-1", client=redis)
    assert [event.event.event for event in events] == ["started", "failed"]
    assert events[-1].event.data == payload


def test_progress_events_append_in_order_and_resume_after_stream_id() -> None:
    redis = FakeRedisSync()

    first = job_store.append_job_event(
        "job-1",
        "tile",
        {"index": 1},
        metric="heavy_metric",
        client=redis,
    )
    second = job_store.append_job_event(
        "job-1",
        "tile",
        {"index": 2},
        metric="heavy_metric",
        client=redis,
    )

    all_events = job_store.read_job_events("job-1", client=redis)
    resumed = job_store.read_job_events("job-1", after_id=first.stream_id, client=redis)

    assert [event.stream_id for event in all_events] == [
        first.stream_id,
        second.stream_id,
    ]
    assert [event.event.data for event in all_events] == [{"index": 1}, {"index": 2}]
    assert [event.stream_id for event in resumed] == [second.stream_id]
    assert _load_status(redis, "job-1")["status"] == "progress"


def test_cancelled_status_is_detected_and_raised() -> None:
    redis = FakeRedisSync()
    job_store.set_job_status("job-1", "cancelled", client=redis)

    assert job_store.is_job_cancelled("job-1", client=redis) is True
    with pytest.raises(job_store.JobCancelledError):
        job_store.raise_if_cancelled("job-1", client=redis)


def test_async_result_read_sanitizes_non_finite_numbers_and_deletes_result() -> None:
    redis = FakeRedisAsync(
        '{"job_id":"job-1","status":"succeeded","result":{"score":NaN}}'
    )

    payload = asyncio.run(job_store.get_job_result_async("job-1", client=redis))
    asyncio.run(job_store.delete_job_result_async("job-1", client=redis))

    assert payload == {
        "job_id": "job-1",
        "status": "succeeded",
        "result": {"score": None},
    }
    assert redis.deleted == [job_store.result_key("job-1")]


def test_async_blocking_event_read_returns_new_stream_entries() -> None:
    redis = FakeRedisAsync()
    asyncio.run(job_store.set_job_status_async("job-1", "queued", client=redis))
    first_id = redis.streams[job_store.events_key("job-1")][0][0]
    asyncio.run(job_store.set_job_status_async("job-1", "started", client=redis))

    events = asyncio.run(
        job_store.read_new_job_events_async(
            "job-1",
            after_id=first_id,
            block_ms=1,
            client=redis,
        )
    )

    assert [event.event.event for event in events] == ["started"]
