import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from lyra.sdk.models import FailedJobResult, JobEnvelope

from lyra_app import job_store
from lyra_app.config import clear_config_cache
from tests.config_helpers import load_test_config


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

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

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        members = sorted(
            self.sorted_sets.get(key, {}),
            key=lambda member: self.sorted_sets[key][member],
            reverse=True,
        )
        return members[start : stop + 1]

    def zrem(self, key: str, *members: str) -> None:
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member in members:
            sorted_set.pop(member, None)

    def zremrangebyscore(self, key: str, min: str | float, max: float) -> None:  # noqa: A002
        lower = float("-inf") if min == "-inf" else float(min)
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member, score in list(sorted_set.items()):
            if lower <= score <= max:
                sorted_set.pop(member, None)

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
        self.sorted_sets: dict[str, dict[str, float]] = {}
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

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(
        self,
        key: str,
        min: str | float,  # noqa: A002
        max: float,  # noqa: A002
    ) -> None:
        lower = float("-inf") if min == "-inf" else float(min)
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member, score in list(sorted_set.items()):
            if lower <= score <= max:
                sorted_set.pop(member, None)

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


@pytest.fixture(autouse=True)
def _load_config(tmp_path: Path) -> Iterator[None]:
    load_test_config(tmp_path)
    yield
    clear_config_cache()


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
    result = FailedJobResult(
        job_id="job-1",
        error={"type": "worker", "message": "boom"},
    )
    payload = job_store.save_job_result(
        result,
        metric="heavy_metric",
        client=redis,
    )

    assert payload == {
        "kind": "failed",
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


def test_job_status_index_lists_recent_jobs_newest_first() -> None:
    redis = FakeRedisSync()

    job_store.set_job_status("job-1", "queued", metric="heavy_metric", client=redis)
    job_store.set_job_status("job-2", "queued", metric="light_metric", client=redis)
    job_store.set_job_status("job-1", "progress", metric="heavy_metric", client=redis)

    jobs = job_store.list_job_statuses(client=redis)

    assert [job.job_id for job in jobs] == ["job-1", "job-2"]
    assert jobs[0].status == "progress"


def test_job_status_index_filters_by_status_and_metric() -> None:
    redis = FakeRedisSync()

    job_store.set_job_status("job-1", "queued", metric="heavy_metric", client=redis)
    job_store.set_job_status("job-2", "started", metric="heavy_metric", client=redis)
    job_store.set_job_status("job-3", "started", metric="light_metric", client=redis)

    started_heavy = job_store.list_job_statuses(
        status="started",
        metric="heavy_metric",
        client=redis,
    )

    assert [job.job_id for job in started_heavy] == ["job-2"]


def test_job_status_index_prunes_expired_members() -> None:
    redis = FakeRedisSync()
    redis.sorted_sets[job_store.job_index_key()] = {"expired": 0.0}

    jobs = job_store.list_job_statuses(client=redis)

    assert jobs == []
    assert redis.sorted_sets[job_store.job_index_key()] == {}


def test_job_status_index_prunes_old_members_on_status_update() -> None:
    redis = FakeRedisSync()
    redis.sorted_sets[job_store.job_index_key()] = {"old-job": 0.0}

    job_store.set_job_status("job-1", "queued", metric="heavy_metric", client=redis)

    assert "old-job" not in redis.sorted_sets[job_store.job_index_key()]
    assert "job-1" in redis.sorted_sets[job_store.job_index_key()]


def test_cancel_job_marks_active_job_cancelled() -> None:
    redis = FakeRedisSync()
    job_store.set_job_status("job-1", "started", metric="heavy_metric", client=redis)

    snapshot, cancelled = job_store.cancel_job("job-1", client=redis)

    assert cancelled is True
    assert snapshot is not None
    assert snapshot.status == "cancelled"
    assert snapshot.metric == "heavy_metric"
    stored_snapshot = job_store.get_job_status("job-1", client=redis)
    assert stored_snapshot is not None
    assert stored_snapshot.status == "cancelled"
    events = job_store.read_job_events("job-1", client=redis)
    assert [event.event.event for event in events] == ["started", "cancelled"]


def test_cancel_job_does_not_overwrite_terminal_result() -> None:
    redis = FakeRedisSync()
    result = FailedJobResult(
        job_id="job-1",
        error={"type": "worker", "message": "boom"},
    )
    payload = job_store.save_job_result(result, metric="heavy_metric", client=redis)

    snapshot, cancelled = job_store.cancel_job("job-1", client=redis)

    assert cancelled is False
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert json.loads(redis.values[job_store.result_key("job-1")]) == payload


def test_cancel_job_returns_missing_for_unknown_job() -> None:
    snapshot, cancelled = job_store.cancel_job("missing", client=FakeRedisSync())

    assert snapshot is None
    assert cancelled is False


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
        '{"kind":"table","job_id":"job-1","status":"succeeded",'
        '"index":["area-1"],"columns":["score"],"data":[[NaN]]}'
    )

    payload = asyncio.run(job_store.get_job_result_async("job-1", client=redis))
    asyncio.run(job_store.delete_job_result_async("job-1", client=redis))

    assert payload == {
        "kind": "table",
        "job_id": "job-1",
        "status": "succeeded",
        "index": ["area-1"],
        "columns": ["score"],
        "data": [[None]],
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
