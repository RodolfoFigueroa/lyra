import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypeAlias

from lyra.sdk.models import (
    JobEnvelope,
    JobEvent,
    ResultDescriptor,
    ResultLifetime,
    TerminalJobResult,
    build_result_descriptor,
    parse_job_result,
)
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field

from lyra_app.config import (
    DEFAULT_JOB_STORE_TTL_SECONDS,
    LyraConfig,
    get_config,
)
from lyra_app.db.redis import redis_client, redis_client_sync

JobStatus: TypeAlias = Literal[
    "queued",
    "started",
    "progress",
    "succeeded",
    "failed",
    "cancelled",
]

TerminalJobStatus: TypeAlias = Literal["succeeded", "failed", "cancelled"]

JOB_STORE_TTL_SECONDS = DEFAULT_JOB_STORE_TTL_SECONDS
STREAM_START = "0-0"
STREAM_LATEST = "$"
DEFAULT_STREAM_BLOCK_MS = 5000
JOB_INDEX_KEY = "jobs:index"
TERMINAL_STATUSES: set[TerminalJobStatus] = {"succeeded", "failed", "cancelled"}


class JobStatusSnapshot(StrictBaseModel):
    job_id: str = Field(min_length=1)
    status: JobStatus
    updated_at: datetime
    metric: str | None = Field(default=None, min_length=1)
    error: dict[str, Any] | None = None


class StoredJobEvent(StrictBaseModel):
    stream_id: str
    event: JobEvent


class JobCancelledError(RuntimeError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"Job {job_id!r} was cancelled.")
        self.job_id = job_id


def status_key(job_id: str) -> str:
    return f"job:{job_id}:status"


def result_key(job_id: str) -> str:
    return f"job:{job_id}:result"


def events_key(job_id: str) -> str:
    return f"job:{job_id}:events"


def job_index_key() -> str:
    return JOB_INDEX_KEY


def _now() -> datetime:
    return datetime.now(UTC)


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload)


def _json_non_finite_constant(_: str) -> None:
    return None


def _loads_json(payload: Any) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode()
    return json.loads(payload, parse_constant=_json_non_finite_constant)


def _job_store_ttl_seconds(config: LyraConfig | None = None) -> int:
    if config is not None:
        return config.job_store.ttl_seconds
    return get_config().job_store.ttl_seconds


def _lifetime_from_ttl_ms(ttl_ms: int | None) -> ResultLifetime:
    if ttl_ms is None or ttl_ms < 0:
        return ResultLifetime()
    return ResultLifetime(
        expires_in_seconds=math.ceil(ttl_ms / 1000),
        expires_at=_now() + timedelta(milliseconds=ttl_ms),
    )


def _lifetime_from_ttl_seconds(ttl_seconds: int | None) -> ResultLifetime:
    if ttl_seconds is None or ttl_seconds < 0:
        return ResultLifetime()
    return ResultLifetime(expires_in_seconds=ttl_seconds)


def get_result_lifetime(
    job_id: str,
    *,
    client: Any | None = None,
) -> ResultLifetime:
    client = redis_client_sync if client is None else client
    key = result_key(job_id)
    pttl = getattr(client, "pttl", None)
    if callable(pttl):
        return _lifetime_from_ttl_ms(pttl(key))
    ttl = getattr(client, "ttl", None)
    if callable(ttl):
        return _lifetime_from_ttl_seconds(ttl(key))
    return ResultLifetime()


async def get_result_lifetime_async(
    job_id: str,
    *,
    client: Any | None = None,
) -> ResultLifetime:
    client = redis_client if client is None else client
    key = result_key(job_id)
    pttl = getattr(client, "pttl", None)
    if callable(pttl):
        return _lifetime_from_ttl_ms(await pttl(key))
    ttl = getattr(client, "ttl", None)
    if callable(ttl):
        return _lifetime_from_ttl_seconds(await ttl(key))
    return ResultLifetime()


def _apply_ttl_sync(client: Any, job_id: str) -> None:
    ttl = _job_store_ttl_seconds()
    client.expire(status_key(job_id), ttl)
    client.expire(result_key(job_id), ttl)
    client.expire(events_key(job_id), ttl)


async def _apply_ttl_async(client: Any, job_id: str) -> None:
    ttl = _job_store_ttl_seconds()
    await client.expire(status_key(job_id), ttl)
    await client.expire(result_key(job_id), ttl)
    await client.expire(events_key(job_id), ttl)


def _prune_job_index_sync(client: Any, *, now: datetime | None = None) -> None:
    cutoff = (now or _now()).timestamp() - _job_store_ttl_seconds()
    client.zremrangebyscore(JOB_INDEX_KEY, "-inf", cutoff)


async def _prune_job_index_async(client: Any, *, now: datetime | None = None) -> None:
    cutoff = (now or _now()).timestamp() - _job_store_ttl_seconds()
    await client.zremrangebyscore(JOB_INDEX_KEY, "-inf", cutoff)


def _index_job_status_sync(client: Any, snapshot: "JobStatusSnapshot") -> None:
    client.zadd(JOB_INDEX_KEY, {snapshot.job_id: snapshot.updated_at.timestamp()})
    _prune_job_index_sync(client, now=snapshot.updated_at)


async def _index_job_status_async(client: Any, snapshot: "JobStatusSnapshot") -> None:
    await client.zadd(JOB_INDEX_KEY, {snapshot.job_id: snapshot.updated_at.timestamp()})
    await _prune_job_index_async(client, now=snapshot.updated_at)


def _decode_job_index_member(member: Any) -> str:
    if isinstance(member, bytes):
        return member.decode()
    return str(member)


def _append_job_event_record_sync(
    job_id: str,
    event: str,
    data: dict[str, Any],
    *,
    client: Any,
) -> StoredJobEvent:
    job_event = JobEvent(
        job_id=job_id,
        event=event,
        timestamp=_now(),
        data=data,
    )
    stream_id = client.xadd(
        events_key(job_id),
        {
            "event": event,
            "payload": _dump_json(job_event.model_dump(mode="json")),
        },
    )
    if isinstance(stream_id, bytes):
        stream_id = stream_id.decode()
    _apply_ttl_sync(client, job_id)
    return StoredJobEvent(stream_id=str(stream_id), event=job_event)


async def _append_job_event_record_async(
    job_id: str,
    event: str,
    data: dict[str, Any],
    *,
    client: Any,
) -> StoredJobEvent:
    job_event = JobEvent(
        job_id=job_id,
        event=event,
        timestamp=_now(),
        data=data,
    )
    stream_id = await client.xadd(
        events_key(job_id),
        {
            "event": event,
            "payload": _dump_json(job_event.model_dump(mode="json")),
        },
    )
    if isinstance(stream_id, bytes):
        stream_id = stream_id.decode()
    await _apply_ttl_async(client, job_id)
    return StoredJobEvent(stream_id=str(stream_id), event=job_event)


def _status_payload(
    job_id: str,
    status: JobStatus,
    *,
    metric: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return JobStatusSnapshot(
        job_id=job_id,
        metric=metric,
        status=status,
        updated_at=_now(),
        error=error,
    ).model_dump(mode="json", exclude_none=True)


def create_job(job: JobEnvelope, client: Any | None = None) -> JobStatusSnapshot:
    return set_job_status(job.job_id, "queued", metric=job.metric, client=client)


def set_job_status(
    job_id: str,
    status: JobStatus,
    *,
    metric: str | None = None,
    error: dict[str, Any] | None = None,
    event_data: dict[str, Any] | None = None,
    emit_event: bool = True,
    client: Any | None = None,
) -> JobStatusSnapshot:
    client = redis_client_sync if client is None else client
    payload = _status_payload(job_id, status, metric=metric, error=error)
    client.set(status_key(job_id), _dump_json(payload), ex=_job_store_ttl_seconds())
    _apply_ttl_sync(client, job_id)
    snapshot = JobStatusSnapshot.model_validate(payload)
    _index_job_status_sync(client, snapshot)
    if emit_event:
        _append_job_event_record_sync(
            job_id,
            status,
            event_data or payload,
            client=client,
        )
    return snapshot


def save_job_result(
    result: TerminalJobResult,
    *,
    metric: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    client = redis_client_sync if client is None else client
    payload = result.model_dump(mode="json", exclude_none=True)
    client.set(
        result_key(result.job_id),
        _dump_json(payload),
        ex=_job_store_ttl_seconds(),
    )
    set_job_status(
        result.job_id,
        result.status,
        metric=metric,
        error=getattr(result, "error", None),
        emit_event=False,
        client=client,
    )
    _append_job_event_record_sync(
        result.job_id,
        result.status,
        payload,
        client=client,
    )
    _apply_ttl_sync(client, result.job_id)
    return payload


def get_job_result(
    job_id: str,
    client: Any | None = None,
) -> dict[str, Any] | None:
    client = redis_client_sync if client is None else client
    payload = client.get(result_key(job_id))
    if payload is None:
        return None
    return _loads_json(payload)


def get_job_result_descriptor(
    job_id: str,
    *,
    client: Any | None = None,
) -> ResultDescriptor | None:
    client = redis_client_sync if client is None else client
    payload = get_job_result(job_id, client=client)
    if payload is None:
        return None
    return build_result_descriptor(
        parse_job_result(payload),
        lifetime=get_result_lifetime(job_id, client=client),
    )


def get_job_status(
    job_id: str,
    client: Any | None = None,
) -> JobStatusSnapshot | None:
    client = redis_client_sync if client is None else client
    payload = client.get(status_key(job_id))
    if payload is None:
        return None
    return JobStatusSnapshot.model_validate(_loads_json(payload))


def is_terminal_status(status: JobStatus) -> bool:
    return status in TERMINAL_STATUSES


def list_job_statuses(
    *,
    limit: int = 50,
    status: JobStatus | None = None,
    metric: str | None = None,
    client: Any | None = None,
) -> list[JobStatusSnapshot]:
    client = redis_client_sync if client is None else client
    _prune_job_index_sync(client)
    jobs: list[JobStatusSnapshot] = []
    stale_job_ids: list[str] = []
    start = 0
    page_size = max(limit * 3, 50)

    while len(jobs) < limit:
        stop = start + page_size - 1
        members = client.zrevrange(JOB_INDEX_KEY, start, stop) or []
        if not members:
            break

        for member in members:
            job_id = _decode_job_index_member(member)
            snapshot = get_job_status(job_id, client=client)
            if snapshot is None:
                stale_job_ids.append(job_id)
                continue
            if status is not None and snapshot.status != status:
                continue
            if metric is not None and snapshot.metric != metric:
                continue
            jobs.append(snapshot)
            if len(jobs) >= limit:
                break

        if len(members) < page_size:
            break
        start += page_size

    if stale_job_ids:
        client.zrem(JOB_INDEX_KEY, *stale_job_ids)
    return jobs


def cancel_job(
    job_id: str,
    *,
    client: Any | None = None,
) -> tuple[JobStatusSnapshot | None, bool]:
    client = redis_client_sync if client is None else client
    snapshot = get_job_status(job_id, client=client)
    if snapshot is None:
        return None, False
    if is_terminal_status(snapshot.status):
        return snapshot, False
    cancelled = set_job_status(
        job_id,
        "cancelled",
        metric=snapshot.metric,
        client=client,
    )
    return cancelled, True


def is_job_cancelled(job_id: str, client: Any | None = None) -> bool:
    snapshot = get_job_status(job_id, client)
    return snapshot is not None and snapshot.status == "cancelled"


def raise_if_cancelled(job_id: str, client: Any | None = None) -> None:
    if is_job_cancelled(job_id, client):
        raise JobCancelledError(job_id)


def append_job_event(
    job_id: str,
    event: str,
    data: dict[str, Any] | None = None,
    *,
    metric: str | None = None,
    client: Any | None = None,
) -> StoredJobEvent:
    client = redis_client_sync if client is None else client
    stored_event = _append_job_event_record_sync(
        event=event,
        data=data or {},
        job_id=job_id,
        client=client,
    )
    set_job_status(job_id, "progress", metric=metric, emit_event=False, client=client)
    _apply_ttl_sync(client, job_id)
    return stored_event


def read_job_events(
    job_id: str,
    *,
    after_id: str | None = None,
    count: int | None = None,
    client: Any | None = None,
) -> list[StoredJobEvent]:
    client = redis_client_sync if client is None else client
    start_id = STREAM_START if after_id is None else f"({after_id}"
    records = client.xrange(events_key(job_id), min=start_id, count=count) or []
    return [_stored_event_from_record(record) for record in records]


async def create_job_async(
    job: JobEnvelope,
    client: Any | None = None,
) -> JobStatusSnapshot:
    return await set_job_status_async(
        job.job_id,
        "queued",
        metric=job.metric,
        client=client,
    )


async def set_job_status_async(
    job_id: str,
    status: JobStatus,
    *,
    metric: str | None = None,
    error: dict[str, Any] | None = None,
    event_data: dict[str, Any] | None = None,
    emit_event: bool = True,
    client: Any | None = None,
) -> JobStatusSnapshot:
    client = redis_client if client is None else client
    payload = _status_payload(job_id, status, metric=metric, error=error)
    await client.set(
        status_key(job_id),
        _dump_json(payload),
        ex=_job_store_ttl_seconds(),
    )
    await _apply_ttl_async(client, job_id)
    snapshot = JobStatusSnapshot.model_validate(payload)
    await _index_job_status_async(client, snapshot)
    if emit_event:
        await _append_job_event_record_async(
            job_id,
            status,
            event_data or payload,
            client=client,
        )
    return snapshot


async def get_job_status_async(
    job_id: str,
    client: Any | None = None,
) -> JobStatusSnapshot | None:
    client = redis_client if client is None else client
    payload = await client.get(status_key(job_id))
    if payload is None:
        return None
    return JobStatusSnapshot.model_validate(_loads_json(payload))


async def get_job_result_async(
    job_id: str,
    client: Any | None = None,
) -> dict[str, Any] | None:
    client = redis_client if client is None else client
    payload = await client.get(result_key(job_id))
    if payload is None:
        return None
    return _loads_json(payload)


async def get_job_result_descriptor_async(
    job_id: str,
    *,
    client: Any | None = None,
) -> ResultDescriptor | None:
    client = redis_client if client is None else client
    payload = await get_job_result_async(job_id, client=client)
    if payload is None:
        return None
    return build_result_descriptor(
        parse_job_result(payload),
        lifetime=await get_result_lifetime_async(job_id, client=client),
    )


async def delete_job_result_async(job_id: str, client: Any | None = None) -> None:
    client = redis_client if client is None else client
    await client.delete(result_key(job_id))


async def read_job_events_async(
    job_id: str,
    *,
    after_id: str | None = None,
    count: int | None = None,
    client: Any | None = None,
) -> list[StoredJobEvent]:
    client = redis_client if client is None else client
    start_id = STREAM_START if after_id is None else f"({after_id}"
    records = await client.xrange(events_key(job_id), min=start_id, count=count) or []
    return [_stored_event_from_record(record) for record in records]


async def read_new_job_events_async(
    job_id: str,
    *,
    after_id: str = STREAM_LATEST,
    block_ms: int = DEFAULT_STREAM_BLOCK_MS,
    count: int | None = None,
    client: Any | None = None,
) -> list[StoredJobEvent]:
    client = redis_client if client is None else client
    response = await client.xread(
        {events_key(job_id): after_id},
        block=block_ms,
        count=count,
    )
    records: list[Any] = []
    for _stream_key, stream_records in response or []:
        if isinstance(stream_records, list):
            records.extend(stream_records)
    return [_stored_event_from_record(record) for record in records]


def _stored_event_from_record(record: Any) -> StoredJobEvent:
    stream_id, fields = record
    if fields is None:
        msg = f"Redis stream record {stream_id!r} did not include fields."
        raise ValueError(msg)
    if isinstance(stream_id, bytes):
        stream_id = stream_id.decode()

    payload = fields.get("payload")
    if payload is None:
        payload = fields.get(b"payload")

    return StoredJobEvent(
        stream_id=str(stream_id),
        event=JobEvent.model_validate(_loads_json(payload)),
    )


__all__ = [
    "JOB_INDEX_KEY",
    "JOB_STORE_TTL_SECONDS",
    "STREAM_LATEST",
    "STREAM_START",
    "TERMINAL_STATUSES",
    "JobCancelledError",
    "JobStatus",
    "JobStatusSnapshot",
    "StoredJobEvent",
    "TerminalJobStatus",
    "append_job_event",
    "cancel_job",
    "create_job",
    "create_job_async",
    "delete_job_result_async",
    "events_key",
    "get_job_result",
    "get_job_result_async",
    "get_job_result_descriptor",
    "get_job_result_descriptor_async",
    "get_job_status",
    "get_job_status_async",
    "get_result_lifetime",
    "get_result_lifetime_async",
    "is_job_cancelled",
    "is_terminal_status",
    "job_index_key",
    "list_job_statuses",
    "raise_if_cancelled",
    "read_job_events",
    "read_job_events_async",
    "read_new_job_events_async",
    "result_key",
    "save_job_result",
    "set_job_status",
    "set_job_status_async",
    "status_key",
]
