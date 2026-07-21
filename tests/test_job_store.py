import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from lyra.sdk.models import (
    CancelledJobResult,
    FailedJobResult,
    FileJobResult,
    JobEnvelope,
    JobRunProvenance,
    ResultReference,
    TableJobResult,
)

from lyra_app import job_store
from lyra_app.config import clear_config_cache
from tests.config_helpers import load_test_config


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.pttl_values: dict[str, int] = {}
        self.ttl_values: dict[str, int] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expirations.append((key, ex))
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    def pttl(self, key: str) -> int:
        return self.pttl_values.get(key, -2)

    def ttl(self, key: str) -> int:
        return self.ttl_values.get(key, -2)

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
        minimum: str,
        /,
        *,
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        records = self.streams.get(key, [])
        if minimum.startswith("("):
            after_id = minimum[1:]
            records = [record for record in records if record[0] > after_id]
        elif minimum != job_store.STREAM_START:
            records = [record for record in records if record[0] >= minimum]
        return records if count is None else records[:count]

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | float,
    ) -> int:
        del script
        assert numkeys == 6
        keys = [str(value) for value in keys_and_args[:numkeys]]
        args = keys_and_args[numkeys:]
        current = self.values.get(keys[0])
        if current is None:
            return 0
        if json.loads(current)["status"] in {"succeeded", "failed", "cancelled"}:
            return 0

        result_payload, status_payload, event_name, event_payload = map(str, args[:4])
        ttl = int(args[4])
        score = float(args[5])
        job_id = str(args[6])
        cutoff = float(args[7])
        self.set(keys[1], result_payload, ex=ttl)
        self.set(keys[0], status_payload, ex=ttl)
        self.xadd(
            keys[2],
            {"event": event_name, "payload": event_payload},
        )
        for key in keys[2:5]:
            self.expire(key, ttl)
        reservation_key = self.values.get(keys[4])
        if reservation_key is not None:
            self.expire(reservation_key, ttl)
        self.zadd(keys[5], {job_id: score})
        self.zremrangebyscore(keys[5], "-inf", cutoff)
        return 1


class FakeRedisAsync:
    def __init__(self, payload: str | None = None) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.pttl_values: dict[str, int] = {}
        self.ttl_values: dict[str, int] = {}
        self.deleted: list[str] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        if payload is not None:
            self.values[job_store.result_key("job-1")] = payload

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expirations.append((key, ex))
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    async def pttl(self, key: str) -> int:
        return self.pttl_values.get(key, -2)

    async def ttl(self, key: str) -> int:
        return self.ttl_values.get(key, -2)

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    async def eval(
        self,
        _script: str,
        _numkeys: int,
        key: str,
        *args: str | int,
    ) -> int | list[int]:
        if not args:
            current = int(self.values.get(key, "0"))
            if current <= 0:
                return 0
            if current == 1:
                await self.delete(key)
            else:
                self.values[key] = str(current - 1)
            return 1
        if len(args) == 1:
            expected = str(args[0])
            if self.values.get(key) != expected:
                return 0
            await self.delete(key)
            return 1

        limit, window_seconds = (int(value) for value in args)
        current = int(self.values.get(key, "0"))
        if current >= limit:
            return [0, current, self.ttl_values.get(key, window_seconds)]
        current += 1
        self.values[key] = str(current)
        if current == 1:
            self.expirations.append((key, window_seconds))
            self.ttl_values[key] = window_seconds
        return [1, current, self.ttl_values[key]]

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
        minimum: str,
        /,
        *,
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        records = self.streams.get(key, [])
        if minimum.startswith("("):
            after_id = minimum[1:]
            records = [record for record in records if record[0] > after_id]
        elif minimum != job_store.STREAM_START:
            records = [record for record in records if record[0] >= minimum]
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


def _provenance() -> JobRunProvenance:
    return JobRunProvenance.model_validate(
        {
            "metric": "heavy_metric",
            "catalog_fingerprint": "catalog-1",
            "plugin": {"name": "fake-plugin", "version": "1.0.0"},
            "input": {
                "location": {"data_type": "met_zone_code", "value": "09.01"},
                "value": 1,
            },
            "output": {
                "kind": "table",
                "columns": [
                    {
                        "name": "value",
                        "type": "integer",
                        "unit": "count",
                        "description": "Example output value.",
                    }
                ],
            },
            "created_at": "2026-07-09T12:00:00Z",
            "row_identity": {
                "field": "cvegeo",
                "namespace": "inegi:cvegeo:ageb",
                "version": "2020",
            },
        }
    )


@pytest.fixture(autouse=True)
def _load_config(tmp_path: Path) -> Iterator[None]:
    load_test_config(tmp_path)
    yield
    clear_config_cache()


def test_create_job_writes_queued_status_and_ttl() -> None:
    redis = FakeRedisSync()
    job = JobEnvelope(job_id="job-1", metric="heavy_metric", input={"value": 1})
    provenance = _provenance()

    snapshot = job_store.create_job(job, provenance, client=redis)

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
    assert (job_store.provenance_key("job-1"), job_store.JOB_STORE_TTL_SECONDS) in (
        redis.expirations
    )
    assert job_store.get_job_provenance("job-1", client=redis) == provenance
    stored = json.loads(redis.values[job_store.provenance_key("job-1")])
    assert stored == provenance.model_dump(mode="json", exclude_none=True)
    assert "coordinates" not in json.dumps(stored)


@pytest.mark.parametrize("active_status", ["queued", "started", "progress"])
def test_save_job_result_if_active_atomically_finalizes_active_job(
    active_status: job_store.JobStatus,
) -> None:
    redis = FakeRedisSync()
    job_store.set_job_status(
        "job-1",
        active_status,
        metric="heavy_metric",
        client=redis,
    )

    saved = job_store.save_job_result_if_active(
        FailedJobResult(
            job_id="job-1",
            error={"type": "worker", "message": "worker disappeared"},
        ),
        client=redis,
    )

    assert saved is True
    assert _load_status(redis, "job-1")["status"] == "failed"
    assert _load_status(redis, "job-1")["metric"] == "heavy_metric"
    assert json.loads(redis.values[job_store.result_key("job-1")])["status"] == (
        "failed"
    )
    events = job_store.read_job_events("job-1", client=redis)
    assert [event.event.event for event in events] == [active_status, "failed"]


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
def test_save_job_result_if_active_does_not_replace_terminal_job(
    terminal_status: job_store.JobStatus,
) -> None:
    redis = FakeRedisSync()
    job_store.set_job_status("job-1", terminal_status, client=redis)

    saved = job_store.save_job_result_if_active(
        FailedJobResult(job_id="job-1", error={"type": "worker"}),
        client=redis,
    )

    assert saved is False
    assert _load_status(redis, "job-1")["status"] == terminal_status
    assert job_store.result_key("job-1") not in redis.values
    assert len(job_store.read_job_events("job-1", client=redis)) == 1


def test_save_job_result_if_active_does_not_resurrect_missing_job() -> None:
    redis = FakeRedisSync()

    saved = job_store.save_job_result_if_active(
        FailedJobResult(job_id="job-1", error={"type": "worker"}),
        client=redis,
    )

    assert saved is False
    assert redis.values == {}


def test_save_job_result_if_active_is_idempotent() -> None:
    redis = FakeRedisSync()
    job_store.set_job_status("job-1", "started", client=redis)
    result = FailedJobResult(job_id="job-1", error={"type": "worker"})

    assert job_store.save_job_result_if_active(result, client=redis) is True
    assert job_store.save_job_result_if_active(result, client=redis) is False
    assert [
        event.event.event for event in job_store.read_job_events("job-1", client=redis)
    ] == ["started", "failed"]


def test_idempotency_claim_is_atomic_scoped_and_conditionally_released() -> None:
    redis = FakeRedisAsync()

    first, acquired = asyncio.run(
        job_store.claim_idempotency_key_async(
            "retry-key",
            "digest-1",
            "job-1",
            client=redis,
        )
    )
    replay, replay_acquired = asyncio.run(
        job_store.claim_idempotency_key_async(
            "retry-key",
            "digest-1",
            "job-2",
            client=redis,
        )
    )
    other_scope, other_scope_acquired = asyncio.run(
        job_store.claim_idempotency_key_async(
            "retry-key",
            "digest-2",
            "job-3",
            agent_scope="other-agent",
            client=redis,
        )
    )

    assert acquired is True
    assert first.job_id == "job-1"
    assert replay_acquired is False
    assert replay == first
    assert other_scope_acquired is True
    assert other_scope.job_id == "job-3"
    assert job_store.idempotency_key("retry-key") != job_store.idempotency_key(
        "retry-key",
        agent_scope="other-agent",
    )
    assert (
        asyncio.run(
            job_store.release_idempotency_key_async(
                "retry-key",
                job_store.IdempotencyRecord(
                    request_digest="different",
                    job_id="job-1",
                ),
                client=redis,
            )
        )
        is False
    )
    assert (
        asyncio.run(
            job_store.release_idempotency_key_async(
                "retry-key",
                first,
                client=redis,
            )
        )
        is True
    )
    assert redis.values.get(job_store.idempotency_key("retry-key")) is None


def test_agent_submission_limit_is_atomic_expires_and_resets_at_boundary() -> None:
    redis = FakeRedisAsync()

    async def consume() -> job_store.AgentSubmissionLimitDecision:
        return await job_store.consume_agent_submission_limit_async(
            limit=2,
            window_seconds=60,
            client=redis,
        )

    first = asyncio.run(consume())
    second = asyncio.run(consume())
    rejected = asyncio.run(consume())

    assert (first.accepted, first.count) == (True, 1)
    assert (second.accepted, second.count) == (True, 2)
    assert rejected.model_dump() == {
        "accepted": False,
        "count": 2,
        "retry_after_seconds": 60,
    }
    key = job_store.agent_submission_limit_key()
    assert key == "jobs:submission-limit:shared-agent"
    assert "secret" not in key
    assert redis.expirations.count((key, 60)) == 1
    assert redis.values[key] == "2"

    redis.values.pop(key)
    redis.ttl_values.pop(key)
    after_boundary = asyncio.run(consume())

    assert (after_boundary.accepted, after_boundary.count) == (True, 1)
    assert redis.expirations.count((key, 60)) == 2

    assert asyncio.run(job_store.release_agent_submission_limit_async(client=redis))
    assert key not in redis.values


def test_job_updates_refresh_idempotency_ttl() -> None:
    redis = FakeRedisAsync()
    asyncio.run(
        job_store.claim_idempotency_key_async(
            "retry-key",
            "digest-1",
            "job-1",
            client=redis,
        )
    )
    redis.expirations.clear()

    asyncio.run(
        job_store.set_job_status_async(
            "job-1",
            "progress",
            client=redis,
        )
    )

    assert (
        job_store.idempotency_key("retry-key"),
        job_store.JOB_STORE_TTL_SECONDS,
    ) in redis.expirations
    assert (
        job_store.job_idempotency_key("job-1"),
        job_store.JOB_STORE_TTL_SECONDS,
    ) in redis.expirations


def test_job_provenance_is_immutable_and_supports_async_reads() -> None:
    redis = FakeRedisAsync()
    job = JobEnvelope(job_id="job-1", metric="heavy_metric", input={"value": 1})
    original = _provenance()
    changed = original.model_copy(update={"catalog_fingerprint": "catalog-2"})

    asyncio.run(job_store.create_job_async(job, original, client=redis))
    asyncio.run(job_store.create_job_async(job, changed, client=redis))

    assert (
        asyncio.run(job_store.get_job_provenance_async("job-1", client=redis))
        == original
    )
    assert (
        asyncio.run(job_store.get_job_provenance_async("missing", client=redis)) is None
    )
    assert json.loads(redis.values[job_store.provenance_key("job-1")]) == (
        original.model_dump(mode="json", exclude_none=True)
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


def test_result_reference_uses_v1_job_uri() -> None:
    reference = ResultReference.for_job_id("job-1")

    assert reference.uri == "lyra://results/job-1"

    with pytest.raises(ValueError, match="result reference"):
        ResultReference(job_id="job-1", uri="lyra://results/other-job")


def test_table_result_descriptor_builds_preview_and_numeric_summary() -> None:
    redis = FakeRedisSync()
    result = TableJobResult(
        job_id="job-1",
        index=["area-1", "area-2", "area-3"],
        columns=["score", "name"],
        data=[[1, "alpha"], [None, "beta"], [3, "alpha"]],
    )
    payload = job_store.save_job_result(result, metric="heavy_metric", client=redis)

    descriptor = job_store.get_job_result_descriptor("job-1", client=redis)

    assert descriptor is not None
    assert descriptor.job_id == "job-1"
    assert descriptor.status == "succeeded"
    assert descriptor.result_kind == "table"
    assert descriptor.result_ref == "lyra://results/job-1"
    assert descriptor.table is not None
    assert descriptor.table.row_count == 3
    assert descriptor.table.column_count == 2
    assert descriptor.table.columns == ["score", "name"]
    assert descriptor.table.index_field == "_result_index"
    assert descriptor.preview.rows == [
        {"_result_index": "area-1", "score": 1, "name": "alpha"},
        {"_result_index": "area-2", "score": None, "name": "beta"},
        {"_result_index": "area-3", "score": 3, "name": "alpha"},
    ]
    assert descriptor.preview.truncated is False
    score_summary = descriptor.summary.columns[0]
    assert score_summary.name == "score"
    assert score_summary.count == 2
    assert score_summary.null_count == 1
    assert score_summary.numeric is not None
    assert score_summary.numeric.model_dump() == {
        "count": 2,
        "null_count": 1,
        "min": 1,
        "max": 3,
        "mean": 2.0,
    }
    name_summary = descriptor.summary.columns[1]
    assert name_summary.name == "name"
    assert name_summary.count == 3
    assert name_summary.null_count == 0
    assert name_summary.numeric is None
    assert json.loads(redis.values[job_store.result_key("job-1")]) == payload


def test_table_descriptor_captures_static_and_batched_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    completed_at = datetime(2026, 7, 9, 12, 5, tzinfo=UTC)
    read_at = datetime(2026, 7, 9, 13, 0, tzinfo=UTC)
    monkeypatch.setattr(job_store, "_now", lambda: completed_at)
    provenance_payload = _provenance().model_dump()
    provenance_payload.update(
        {
            "input": {
                "location": {"data_type": "met_zone_code", "value": "09.01"},
                "sector_filters": [
                    {
                        "key": "retail",
                        "value": "^46.*",
                        "label": "Retail jobs",
                    },
                    {"key": "health", "value": "^62.*"},
                ],
            },
            "output": {
                "kind": "table",
                "columns": [
                    {
                        "name": "population",
                        "type": "integer",
                        "unit": "people",
                        "description": "Resident population.",
                    }
                ],
                "batched_columns": [
                    {
                        "source": "sector_filters",
                        "name": "accessibility_{key}",
                        "type": "number",
                        "unit": "jobs",
                        "description": "Accessibility for {label}.",
                        "nullable": True,
                    }
                ],
            },
        }
    )
    provenance = JobRunProvenance.model_validate(provenance_payload)
    job = JobEnvelope(job_id="job-1", metric=provenance.metric, input={})
    job_store.create_job(job, provenance, client=redis)
    job_store.save_job_result(
        TableJobResult(
            job_id="job-1",
            index=["area-1"],
            columns=[
                "population",
                "accessibility_retail",
                "accessibility_health",
            ],
            data=[[100, 8.5, None]],
        ),
        client=redis,
    )

    descriptor = job_store.get_job_result_descriptor("job-1", client=redis)
    monkeypatch.setattr(job_store, "_now", lambda: read_at)
    reread = job_store.get_job_result_descriptor("job-1", client=redis)

    assert descriptor is not None
    assert reread is not None
    assert descriptor.schema_version == 1
    assert descriptor.provenance == provenance
    assert descriptor.completed_at == completed_at
    assert reread.completed_at == completed_at
    assert descriptor.table is not None
    assert descriptor.table.row_identity == provenance.row_identity
    assert [column.model_dump() for column in descriptor.table.column_contracts] == [
        {
            "name": "population",
            "type": "integer",
            "unit": "people",
            "description": "Resident population.",
            "nullable": False,
        },
        {
            "name": "accessibility_retail",
            "type": "number",
            "unit": "jobs",
            "description": "Accessibility for Retail jobs.",
            "nullable": True,
        },
        {
            "name": "accessibility_health",
            "type": "number",
            "unit": "jobs",
            "description": "Accessibility for health.",
            "nullable": True,
        },
    ]


def test_file_descriptor_retains_run_provenance_without_table_columns(
    tmp_path: Path,
) -> None:
    redis = FakeRedisSync()
    provenance_payload = _provenance().model_dump()
    provenance_payload.update(
        {
            "output": {
                "kind": "file",
                "media_type": "image/tiff",
                "extensions": [".tif"],
            },
            "row_identity": None,
        }
    )
    provenance = JobRunProvenance.model_validate(provenance_payload)
    job_store.create_job(
        JobEnvelope(job_id="job-1", metric=provenance.metric, input={}),
        provenance,
        client=redis,
    )
    job_store.save_job_result(
        FileJobResult(
            job_id="job-1",
            file_path=str(tmp_path / "result.tif"),
            media_type="image/tiff",
        ),
        client=redis,
    )

    descriptor = job_store.get_job_result_descriptor("job-1", client=redis)

    assert descriptor is not None
    assert descriptor.provenance == provenance
    assert descriptor.table is None
    assert descriptor.file is not None
    assert descriptor.file.media_type == "image/tiff"


def test_table_descriptor_expands_fractional_area_column_contract() -> None:
    redis = FakeRedisSync()
    provenance_payload = _provenance().model_dump()
    provenance_payload["output"] = {
        "kind": "table",
        "columns": [
            {
                "name": "covered_area_m2",
                "type": "number",
                "unit": "m2",
                "description": "Covered area in square metres.",
                "derivations": [
                    {
                        "kind": "fraction_of_location_area",
                        "name": "covered_area_fraction",
                        "description": "Fraction of the location covered.",
                    }
                ],
            }
        ],
    }
    provenance = JobRunProvenance.model_validate(provenance_payload)
    job_store.create_job(
        JobEnvelope(job_id="job-area", metric=provenance.metric, input={}),
        provenance,
        client=redis,
    )
    job_store.save_job_result(
        TableJobResult(
            job_id="job-area",
            index=["area-1"],
            columns=["covered_area_m2", "covered_area_fraction"],
            data=[[25.0, 0.25]],
        ),
        client=redis,
    )

    descriptor = job_store.get_job_result_descriptor("job-area", client=redis)

    assert descriptor is not None
    assert descriptor.table is not None
    contracts = descriptor.table.column_contracts
    assert [column.name for column in contracts] == [
        "covered_area_m2",
        "covered_area_fraction",
    ]
    assert contracts[1].unit == "ratio"
    assert descriptor.preview.rows[0]["covered_area_fraction"] == 0.25
    assert [column.name for column in descriptor.summary.columns] == [
        "covered_area_m2",
        "covered_area_fraction",
    ]


def test_table_preview_uses_collision_free_named_index_field() -> None:
    redis = FakeRedisSync()
    job_store.save_job_result(
        TableJobResult(
            job_id="job-1",
            index=["area-1"],
            columns=["_result_index", "value"],
            data=[["column-value", 1]],
        ),
        client=redis,
    )

    descriptor = job_store.get_job_result_descriptor("job-1", client=redis)

    assert descriptor is not None
    assert descriptor.table is not None
    assert descriptor.table.index_field == "__result_index"
    assert descriptor.preview.rows == [
        {"__result_index": "area-1", "_result_index": "column-value", "value": 1}
    ]


def test_result_descriptor_uses_pttl_for_exact_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    fixed_now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(job_store, "_now", lambda: fixed_now)
    job_store.save_job_result(
        TableJobResult(
            job_id="job-1",
            index=["area-1"],
            columns=["score"],
            data=[[1]],
        ),
        client=redis,
    )
    redis.pttl_values[job_store.result_key("job-1")] = 90_500

    descriptor = job_store.get_job_result_descriptor("job-1", client=redis)

    assert descriptor is not None
    assert descriptor.lifetime.expires_in_seconds == 91
    assert descriptor.lifetime.expires_at == datetime(
        2026,
        7,
        8,
        12,
        1,
        30,
        500000,
        tzinfo=UTC,
    )


def test_result_descriptor_uses_ttl_seconds_without_guessing_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    monkeypatch.setattr(redis, "pttl", None)
    job_store.set_job_status("job-1", "succeeded", client=redis)
    redis.ttl_values[job_store.result_key("job-1")] = 90
    redis.values[job_store.result_key("job-1")] = json.dumps(
        TableJobResult(
            job_id="job-1",
            index=["area-1"],
            columns=["score"],
            data=[[1]],
        ).model_dump(mode="json", exclude_none=True)
    )

    descriptor = job_store.get_job_result_descriptor("job-1", client=redis)

    assert descriptor is not None
    assert descriptor.lifetime.expires_in_seconds == 90
    assert descriptor.lifetime.expires_at is None


@pytest.mark.parametrize(
    ("result", "expected_error"),
    [
        (
            FailedJobResult(
                job_id="job-1",
                error={"type": "worker", "message": "boom"},
            ),
            {"type": "worker", "message": "boom"},
        ),
        (CancelledJobResult(job_id="job-1"), None),
        (
            CancelledJobResult(
                job_id="job-1",
                error={"type": "cancelled", "message": "stopped"},
            ),
            {"type": "cancelled", "message": "stopped"},
        ),
    ],
)
def test_descriptor_reports_failed_and_cancelled_terminal_results(
    result: FailedJobResult | CancelledJobResult,
    expected_error: dict[str, Any] | None,
) -> None:
    redis = FakeRedisSync()
    provenance = _provenance()
    job_store.create_job(
        JobEnvelope(job_id="job-1", metric=provenance.metric, input={}),
        provenance,
        client=redis,
    )
    payload = job_store.save_job_result(result, client=redis)

    descriptor = job_store.get_job_result_descriptor("job-1", client=redis)

    assert descriptor is not None
    assert descriptor.result_ref == "lyra://results/job-1"
    assert descriptor.result_kind == result.kind
    assert descriptor.status == result.status
    assert descriptor.preview.rows == []
    assert descriptor.summary.kind == result.kind
    assert descriptor.summary.error == expected_error
    assert descriptor.error == expected_error
    assert descriptor.provenance == provenance
    assert descriptor.table is None
    assert json.loads(redis.values[job_store.result_key("job-1")]) == payload


def test_async_result_descriptor_reads_stored_result_and_lifetime() -> None:
    redis = FakeRedisAsync(
        json.dumps(
            TableJobResult(
                job_id="job-1",
                index=["area-1"],
                columns=["score"],
                data=[[4]],
            ).model_dump(mode="json", exclude_none=True)
        )
    )
    asyncio.run(job_store.set_job_status_async("job-1", "succeeded", client=redis))
    redis.pttl_values[job_store.result_key("job-1")] = 1_500

    descriptor = asyncio.run(
        job_store.get_job_result_descriptor_async("job-1", client=redis)
    )

    assert descriptor is not None
    assert descriptor.result_ref == "lyra://results/job-1"
    assert descriptor.preview.rows == [{"_result_index": "area-1", "score": 4}]
    assert descriptor.lifetime.expires_in_seconds == 2


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
