from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeAlias,
    TypedDict,
    TypeVar,
    Unpack,
    cast,
    runtime_checkable,
)

from lyra.sdk.models import (
    JobEnvelope,
    JobEvent,
    JobLifecycleEvent,
    JobLifecycleStatus,
    JobMessage,
    JobMessageEvent,
    JobProgress,
    JobProgressEvent,
    JobRunProvenance,
    ResultDescriptor,
    ResultLifetime,
    TerminalJobResult,
    build_result_descriptor,
    parse_job_event,
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

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from lyra.sdk.types import JsonValue

JobStatus: TypeAlias = JobLifecycleStatus

TerminalJobStatus: TypeAlias = Literal["succeeded", "failed", "cancelled"]


class JobStatusOptions(TypedDict, total=False):
    """Optional persistence and event controls for a status update."""

    metric: str | None
    error: dict[str, Any] | None
    client: SyncAtomicJobWriter | None


class AsyncJobStatusOptions(TypedDict, total=False):
    """Async persistence and event controls for a status update."""

    metric: str | None
    error: dict[str, Any] | None
    client: AsyncAtomicJobWriter | None


JOB_STORE_TTL_SECONDS = DEFAULT_JOB_STORE_TTL_SECONDS
STREAM_START = "0-0"
STREAM_LATEST = "$"
DEFAULT_STREAM_BLOCK_MS = 5000
JOB_INDEX_KEY = "jobs:index"
TERMINAL_STATUSES: set[TerminalJobStatus] = {"succeeded", "failed", "cancelled"}
DEFAULT_AGENT_SCOPE = "shared-agent"
AGENT_SUBMISSION_LIMIT_KEY = "jobs:submission-limit:shared-agent"
logger = logging.getLogger(__name__)

_APPEND_JOB_EVENT_SCRIPT = """
local current = redis.call('get', KEYS[1])
local guard = ARGV[1]
if guard == 'missing' and current then
    return 'error:exists'
end
if guard ~= 'missing' then
    if not current then
        return 'error:missing'
    end
    local current_status = cjson.decode(current)['status']
    if current_status == 'succeeded'
        or current_status == 'failed'
        or current_status == 'cancelled' then
        return 'error:terminal:' .. current_status
    end
    if guard == 'queued' and current_status ~= 'queued' then
        return 'error:expected-queued:' .. current_status
    end
    if guard == 'running' and current_status ~= 'running' then
        return 'error:expected-running:' .. current_status
    end
end

local ttl = tonumber(ARGV[5])
redis.call('set', KEYS[1], ARGV[2], 'EX', ttl)
local stream_id = redis.call(
    'xadd', KEYS[2], 'MAXLEN', '~', tonumber(ARGV[9]), '*',
    'event', ARGV[3], 'payload', ARGV[4]
)
redis.call('expire', KEYS[2], ttl)
redis.call('expire', KEYS[3], ttl)
local reservation_key = redis.call('get', KEYS[4])
if reservation_key then
    redis.call('expire', reservation_key, ttl)
end
redis.call('expire', KEYS[4], ttl)
redis.call('zadd', KEYS[5], ARGV[6], ARGV[7])
redis.call('zremrangebyscore', KEYS[5], '-inf', ARGV[8])
return stream_id
""".strip()

_CONSUME_AGENT_SUBMISSION_LIMIT_SCRIPT = """
local limit = tonumber(ARGV[1])
local window_seconds = tonumber(ARGV[2])
local current = tonumber(redis.call('get', KEYS[1]) or '0')

if current >= limit then
    local retry_after_seconds = redis.call('ttl', KEYS[1])
    if retry_after_seconds < 0 then
        redis.call('expire', KEYS[1], window_seconds)
        retry_after_seconds = window_seconds
    elseif retry_after_seconds < 1 then
        retry_after_seconds = 1
    end
    return {0, current, retry_after_seconds}
end

current = redis.call('incr', KEYS[1])
if current == 1 then
    redis.call('expire', KEYS[1], window_seconds)
end

local retry_after_seconds = redis.call('ttl', KEYS[1])
if retry_after_seconds < 0 then
    redis.call('expire', KEYS[1], window_seconds)
    retry_after_seconds = window_seconds
end
return {1, current, retry_after_seconds}
""".strip()

_RELEASE_AGENT_SUBMISSION_LIMIT_SCRIPT = """
local current = tonumber(redis.call('get', KEYS[1]) or '0')
if current <= 0 then
    return 0
end
if current == 1 then
    redis.call('del', KEYS[1])
    return 1
end
redis.call('decr', KEYS[1])
return 1
""".strip()

_SAVE_TERMINAL_RESULT_IF_ACTIVE_SCRIPT = """
local current = redis.call('get', KEYS[1])
if not current then
    return 0
end

local current_status = cjson.decode(current)['status']
if current_status == 'succeeded'
    or current_status == 'failed'
    or current_status == 'cancelled' then
    return 0
end

local ttl = tonumber(ARGV[5])
redis.call('set', KEYS[2], ARGV[1], 'EX', ttl)
redis.call('set', KEYS[1], ARGV[2], 'EX', ttl)
redis.call(
    'xadd', KEYS[3], 'MAXLEN', '~', tonumber(ARGV[9]), '*',
    'event', ARGV[3],
    'payload', ARGV[4]
)
redis.call('expire', KEYS[3], ttl)
redis.call('expire', KEYS[4], ttl)

local reservation_key = redis.call('get', KEYS[5])
if reservation_key then
    redis.call('expire', reservation_key, ttl)
end
redis.call('expire', KEYS[5], ttl)

redis.call('zadd', KEYS[6], ARGV[6], ARGV[7])
redis.call('zremrangebyscore', KEYS[6], '-inf', ARGV[8])
return 1
""".strip()

_RELEASE_IDEMPOTENCY_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
""".strip()

RedisPayload: TypeAlias = str | bytes
RedisScriptResult: TypeAlias = RedisPayload | int | list[int]
RedisStreamFields: TypeAlias = Mapping[str, RedisPayload] | Mapping[bytes, RedisPayload]
RedisStreamRecord: TypeAlias = tuple[RedisPayload, RedisStreamFields]


@runtime_checkable
class SyncKeyReader(Protocol):
    def get(self, key: str) -> RedisPayload | None: ...


@runtime_checkable
class AsyncKeyReader(Protocol):
    def get(self, key: str) -> Awaitable[RedisPayload | None]: ...


@runtime_checkable
class SyncMillisecondLifetimeReader(Protocol):
    def pttl(self, key: str) -> int: ...


@runtime_checkable
class SyncSecondLifetimeReader(Protocol):
    def ttl(self, key: str) -> int: ...


@runtime_checkable
class AsyncMillisecondLifetimeReader(Protocol):
    def pttl(self, key: str) -> Awaitable[int]: ...


@runtime_checkable
class AsyncSecondLifetimeReader(Protocol):
    def ttl(self, key: str) -> Awaitable[int]: ...


class SyncJobWriter(Protocol):
    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool = False,
    ) -> bool | None: ...

    def expire(self, key: str, ttl: int) -> bool | None: ...

    def xadd(self, key: str, fields: dict[str, str]) -> RedisPayload: ...

    def zadd(self, key: str, mapping: dict[str, float]) -> int | None: ...

    def zremrangebyscore(
        self,
        key: str,
        minimum: str | float,
        maximum: float,
        /,
    ) -> int | None: ...


class AsyncJobWriter(Protocol):
    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool = False,
    ) -> Awaitable[bool | None]: ...

    def expire(self, key: str, ttl: int) -> Awaitable[bool | None]: ...

    def xadd(
        self,
        key: str,
        fields: dict[str, str],
    ) -> Awaitable[RedisPayload]: ...

    def zadd(
        self,
        key: str,
        mapping: dict[str, float],
    ) -> Awaitable[int | None]: ...

    def zremrangebyscore(
        self,
        key: str,
        minimum: str | float,
        maximum: float,
        /,
    ) -> Awaitable[int | None]: ...


class SyncAtomicJobWriter(SyncJobWriter, SyncKeyReader, Protocol):
    def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        /,
        *keys_and_args: str | float,
    ) -> RedisScriptResult: ...


class AsyncAtomicJobWriter(AsyncJobWriter, AsyncKeyReader, Protocol):
    def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        /,
        *keys_and_args: str | float,
    ) -> Awaitable[RedisScriptResult]: ...


class SyncJobClient(SyncJobWriter, SyncKeyReader, Protocol):
    pass


class SyncConditionalJobWriter(SyncJobClient, Protocol):
    def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        /,
        *keys_and_args: str | float,
    ) -> RedisScriptResult: ...


class SyncJobListClient(SyncKeyReader, Protocol):
    def zrevrange(self, key: str, start: int, stop: int) -> Sequence[RedisPayload]: ...

    def zrem(self, key: str, *members: str) -> int | None: ...

    def zremrangebyscore(
        self,
        key: str,
        minimum: str | float,
        maximum: float,
        /,
    ) -> int | None: ...


class SyncEventReader(Protocol):
    def xrange(
        self,
        key: str,
        minimum: str,
        /,
        *,
        count: int | None = None,
    ) -> Sequence[RedisStreamRecord]: ...


class AsyncEventReader(Protocol):
    def xrange(
        self,
        key: str,
        minimum: str,
        /,
        *,
        count: int | None = None,
    ) -> Awaitable[Sequence[RedisStreamRecord]]: ...


class AsyncNewEventReader(Protocol):
    def xread(
        self,
        streams: dict[str, str],
        *,
        block: int,
        count: int | None = None,
    ) -> Awaitable[Sequence[tuple[RedisPayload, Sequence[RedisStreamRecord]]]]: ...


class AsyncDeleteClient(Protocol):
    def delete(self, key: str) -> Awaitable[int | None]: ...


class AsyncScriptClient(Protocol):
    def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        /,
        *args: str | float,
    ) -> Awaitable[RedisScriptResult]: ...


class AsyncIdempotencyClient(
    AsyncKeyReader, AsyncDeleteClient, AsyncScriptClient, Protocol
):
    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool = False,
    ) -> Awaitable[bool | None]: ...


RedisClientT = TypeVar("RedisClientT")


def _default_sync_client(client: RedisClientT | None) -> RedisClientT:
    if client is not None:
        return client
    return cast("RedisClientT", redis_client_sync)


def _default_async_client(client: RedisClientT | None) -> RedisClientT:
    if client is not None:
        return client
    return cast("RedisClientT", redis_client)


class JobStatusSnapshot(StrictBaseModel):
    job_id: str = Field(min_length=1)
    status: JobStatus
    updated_at: datetime
    metric: str | None = Field(default=None, min_length=1)
    error: dict[str, Any] | None = None
    progress: JobProgress | None = None
    latest_message: JobMessage | None = None


class StoredJobEvent(StrictBaseModel):
    stream_id: str
    event: JobEvent


class IdempotencyRecord(StrictBaseModel):
    request_digest: str = Field(min_length=1)
    job_id: str = Field(min_length=1)


class AgentSubmissionLimitDecision(StrictBaseModel):
    accepted: bool
    count: int = Field(ge=0)
    retry_after_seconds: int = Field(ge=1)


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


def provenance_key(job_id: str) -> str:
    return f"job:{job_id}:provenance"


def idempotency_key(
    caller_key: str,
    *,
    agent_scope: str = DEFAULT_AGENT_SCOPE,
) -> str:
    digest = hashlib.sha256(
        f"{agent_scope}\0{caller_key}".encode(),
    ).hexdigest()
    return f"jobs:idempotency:{digest}"


def job_idempotency_key(job_id: str) -> str:
    return f"job:{job_id}:idempotency"


def job_index_key() -> str:
    return JOB_INDEX_KEY


def agent_submission_limit_key() -> str:
    """Return the non-secret Redis key shared by all agent submissions."""
    return AGENT_SUBMISSION_LIMIT_KEY


async def consume_agent_submission_limit_async(
    *,
    limit: int,
    window_seconds: int,
    client: AsyncScriptClient | None = None,
) -> AgentSubmissionLimitDecision:
    """Atomically consume capacity from the shared agent fixed window."""
    client = _default_async_client(client)
    raw_decision = await client.eval(
        _CONSUME_AGENT_SUBMISSION_LIMIT_SCRIPT,
        1,
        agent_submission_limit_key(),
        limit,
        window_seconds,
    )
    if not isinstance(raw_decision, list) or len(raw_decision) != 3:
        msg = "Redis submission-limit script returned an invalid response"
        raise RuntimeError(msg)
    accepted, count, retry_after_seconds = (int(value) for value in raw_decision)
    return AgentSubmissionLimitDecision(
        accepted=bool(accepted),
        count=count,
        retry_after_seconds=max(1, retry_after_seconds),
    )


async def release_agent_submission_limit_async(
    *,
    client: AsyncScriptClient | None = None,
) -> bool:
    """Return capacity when a consumed submission fails before dispatch."""
    client = _default_async_client(client)
    released = await client.eval(
        _RELEASE_AGENT_SUBMISSION_LIMIT_SCRIPT,
        1,
        agent_submission_limit_key(),
    )
    return bool(released)


def _now() -> datetime:
    return datetime.now(UTC)


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _encode_event(event: JobEvent) -> str:
    payload = _dump_json(event.model_dump(mode="json", exclude_none=True))
    limit = get_config().job_events.max_payload_bytes
    size = len(payload.encode())
    if size > limit:
        msg = f"Job event payload is {size} bytes; maximum is {limit}."
        raise ValueError(msg)
    return payload


def _log_job_event(event: JobEvent, *, progress_level: int = logging.DEBUG) -> None:
    fields: dict[str, Any] = {
        "event_kind": event.kind,
        "job_id": event.job_id,
        "metric": event.metric,
    }
    level = logging.INFO
    message = "Job lifecycle changed"
    if isinstance(event, JobLifecycleEvent):
        fields["job_status"] = event.status
        if event.error is not None:
            fields["error"] = event.error
    elif isinstance(event, JobProgressEvent):
        level = progress_level
        message = event.message or f"Job progress: {event.stage}"
        fields.update(
            {
                "stage": event.stage,
                "progress_current": event.current,
                "progress_total": event.total,
                "progress_unit": event.unit,
            }
        )
    else:
        level = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
        }[event.level]
        message = event.message
        fields["message_level"] = event.level
        fields["event_fields"] = event.fields
    logger.log(level, message, extra={"structured_fields": fields})


def _json_non_finite_constant(_: str) -> None:
    return None


def _loads_json(payload: RedisPayload) -> JsonValue:
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
    client: SyncKeyReader | None = None,
) -> ResultLifetime:
    client = _default_sync_client(client)
    key = result_key(job_id)
    if isinstance(client, SyncMillisecondLifetimeReader) and callable(client.pttl):
        return _lifetime_from_ttl_ms(client.pttl(key))
    if isinstance(client, SyncSecondLifetimeReader) and callable(client.ttl):
        return _lifetime_from_ttl_seconds(client.ttl(key))
    return ResultLifetime()


async def get_result_lifetime_async(
    job_id: str,
    *,
    client: AsyncKeyReader | None = None,
) -> ResultLifetime:
    client = _default_async_client(client)
    key = result_key(job_id)
    if isinstance(client, AsyncMillisecondLifetimeReader):
        return _lifetime_from_ttl_ms(await client.pttl(key))
    if isinstance(client, AsyncSecondLifetimeReader):
        return _lifetime_from_ttl_seconds(await client.ttl(key))
    return ResultLifetime()


async def claim_idempotency_key_async(
    caller_key: str,
    request_digest: str,
    job_id: str,
    *,
    agent_scope: str = DEFAULT_AGENT_SCOPE,
    client: AsyncIdempotencyClient | None = None,
) -> tuple[IdempotencyRecord, bool]:
    """Atomically bind one caller key to a request digest and job identity."""
    client = _default_async_client(client)
    key = idempotency_key(caller_key, agent_scope=agent_scope)
    record = IdempotencyRecord(request_digest=request_digest, job_id=job_id)
    encoded = _dump_json(record.model_dump(mode="json"))
    ttl = _job_store_ttl_seconds()
    acquired = await client.set(key, encoded, ex=ttl, nx=True)
    if acquired:
        try:
            await client.set(job_idempotency_key(job_id), key, ex=ttl)
        except BaseException:
            await client.eval(_RELEASE_IDEMPOTENCY_SCRIPT, 1, key, encoded)
            raise
        return record, True

    existing = await client.get(key)
    if existing is None:
        # The prior record expired between SET and GET. Retry the atomic claim.
        return await claim_idempotency_key_async(
            caller_key,
            request_digest,
            job_id,
            agent_scope=agent_scope,
            client=client,
        )
    return IdempotencyRecord.model_validate(_loads_json(existing)), False


async def release_idempotency_key_async(
    caller_key: str,
    record: IdempotencyRecord,
    *,
    agent_scope: str = DEFAULT_AGENT_SCOPE,
    client: AsyncIdempotencyClient | None = None,
) -> bool:
    """Release only the exact reservation owned by ``record``."""
    client = _default_async_client(client)
    key = idempotency_key(caller_key, agent_scope=agent_scope)
    encoded = _dump_json(record.model_dump(mode="json"))
    released = await client.eval(_RELEASE_IDEMPOTENCY_SCRIPT, 1, key, encoded)
    if released:
        await client.delete(job_idempotency_key(record.job_id))
    return bool(released)


def _prune_job_index_sync(
    client: SyncJobWriter | SyncJobListClient,
    *,
    now: datetime | None = None,
) -> None:
    cutoff = (now or _now()).timestamp() - _job_store_ttl_seconds()
    client.zremrangebyscore(JOB_INDEX_KEY, "-inf", cutoff)


def _decode_job_index_member(member: RedisPayload) -> str:
    if isinstance(member, bytes):
        return member.decode()
    return str(member)


def _build_status_snapshot(
    job_id: str,
    status: JobStatus,
    *,
    previous: JobStatusSnapshot | None,
    metric: str | None = None,
    error: dict[str, Any] | None = None,
) -> JobStatusSnapshot:
    return JobStatusSnapshot(
        job_id=job_id,
        metric=metric if metric is not None else getattr(previous, "metric", None),
        status=status,
        updated_at=_now(),
        error=error,
        progress=getattr(previous, "progress", None),
        latest_message=getattr(previous, "latest_message", None),
    )


def _decode_script_result(result: RedisScriptResult) -> str:
    if isinstance(result, list):
        msg = "Job event script returned an unexpected list result."
        raise TypeError(msg)
    if isinstance(result, bytes):
        return result.decode()
    return str(result)


def _event_keys(job_id: str) -> tuple[str, str, str, str, str]:
    return (
        status_key(job_id),
        events_key(job_id),
        provenance_key(job_id),
        job_idempotency_key(job_id),
        job_index_key(),
    )


def _append_event_and_snapshot_sync(
    snapshot: JobStatusSnapshot,
    event: JobEvent,
    *,
    guard: str,
    client: SyncAtomicJobWriter,
) -> StoredJobEvent | None:
    encoded_event = _encode_event(event)
    ttl = _job_store_ttl_seconds()
    result = client.eval(
        _APPEND_JOB_EVENT_SCRIPT,
        5,
        *_event_keys(snapshot.job_id),
        guard,
        _dump_json(snapshot.model_dump(mode="json", exclude_none=True)),
        event.kind,
        encoded_event,
        ttl,
        snapshot.updated_at.timestamp(),
        snapshot.job_id,
        snapshot.updated_at.timestamp() - ttl,
        get_config().job_events.max_stream_events,
    )
    value = _decode_script_result(result)
    if value.startswith("error:"):
        return None
    return StoredJobEvent(stream_id=value, event=event)


async def _append_event_and_snapshot_async(
    snapshot: JobStatusSnapshot,
    event: JobEvent,
    *,
    guard: str,
    client: AsyncAtomicJobWriter,
) -> StoredJobEvent | None:
    encoded_event = _encode_event(event)
    ttl = _job_store_ttl_seconds()
    result = await client.eval(
        _APPEND_JOB_EVENT_SCRIPT,
        5,
        *_event_keys(snapshot.job_id),
        guard,
        _dump_json(snapshot.model_dump(mode="json", exclude_none=True)),
        event.kind,
        encoded_event,
        ttl,
        snapshot.updated_at.timestamp(),
        snapshot.job_id,
        snapshot.updated_at.timestamp() - ttl,
        get_config().job_events.max_stream_events,
    )
    value = _decode_script_result(result)
    if value.startswith("error:"):
        return None
    return StoredJobEvent(stream_id=value, event=event)


def _save_job_provenance_sync(
    job_id: str,
    provenance: JobRunProvenance,
    *,
    client: SyncJobWriter,
) -> None:
    payload = provenance.model_dump(mode="json", exclude_none=True)
    client.set(
        provenance_key(job_id),
        _dump_json(payload),
        ex=_job_store_ttl_seconds(),
        nx=True,
    )


async def _save_job_provenance_async(
    job_id: str,
    provenance: JobRunProvenance,
    *,
    client: AsyncJobWriter,
) -> None:
    payload = provenance.model_dump(mode="json", exclude_none=True)
    await client.set(
        provenance_key(job_id),
        _dump_json(payload),
        ex=_job_store_ttl_seconds(),
        nx=True,
    )


def create_job(
    job: JobEnvelope,
    provenance: JobRunProvenance | None = None,
    client: SyncAtomicJobWriter | None = None,
) -> JobStatusSnapshot:
    client = _default_sync_client(client)
    if provenance is not None:
        _save_job_provenance_sync(job.job_id, provenance, client=client)
    return set_job_status(job.job_id, "queued", metric=job.metric, client=client)


def set_job_status(
    job_id: str,
    status: JobStatus,
    **options: Unpack[JobStatusOptions],
) -> JobStatusSnapshot:
    metric = options.get("metric")
    error = options.get("error")
    client = options.get("client")
    client = _default_sync_client(client)
    current = get_job_status(job_id, client=client)
    snapshot = _build_status_snapshot(
        job_id,
        status,
        previous=current,
        metric=metric,
        error=error,
    )
    event = JobLifecycleEvent(
        job_id=job_id,
        metric=snapshot.metric,
        timestamp=snapshot.updated_at,
        status=status,
        error=error,
    )
    guard = (
        "missing"
        if status == "queued"
        else "queued"
        if status == "running"
        else "active"
    )
    stored = _append_event_and_snapshot_sync(
        snapshot,
        event,
        guard=guard,
        client=client,
    )
    if stored is None:
        latest = get_job_status(job_id, client=client)
        if latest is not None and latest.status == "cancelled":
            raise JobCancelledError(job_id)
        msg = f"Invalid job lifecycle transition for {job_id!r} to {status!r}."
        raise RuntimeError(msg)
    _log_job_event(event)
    return snapshot


def save_job_result(
    result: TerminalJobResult,
    *,
    metric: str | None = None,
    client: SyncAtomicJobWriter | None = None,
) -> dict[str, Any]:
    client = _default_sync_client(client)
    payload = result.model_dump(mode="json", exclude_none=True)
    current = get_job_status(result.job_id, client=client)
    if current is not None and is_terminal_status(current.status):
        if current.status != result.status:
            msg = f"Job {result.job_id!r} already has a different terminal status."
            raise RuntimeError(msg)
        client.set(
            result_key(result.job_id),
            _dump_json(payload),
            ex=_job_store_ttl_seconds(),
            nx=True,
        )
        return payload
    if current is None:
        set_job_status(
            result.job_id,
            "queued",
            metric=metric,
            client=client,
        )
    if not save_job_result_if_active(result, client=client):
        msg = f"Job {result.job_id!r} already has a terminal result."
        raise RuntimeError(msg)
    return payload


def save_job_result_if_active(
    result: TerminalJobResult,
    *,
    client: SyncConditionalJobWriter | None = None,
) -> bool:
    """Atomically persist a terminal result unless the job already finished."""
    client = _default_sync_client(client)
    snapshot = get_job_status(result.job_id, client=client)
    if snapshot is None or is_terminal_status(snapshot.status):
        return False

    now = _now()
    result_payload = result.model_dump(mode="json", exclude_none=True)
    status_payload = JobStatusSnapshot(
        job_id=result.job_id,
        status=result.status,
        updated_at=now,
        metric=snapshot.metric,
        error=getattr(result, "error", None),
        progress=snapshot.progress,
        latest_message=snapshot.latest_message,
    ).model_dump(mode="json", exclude_none=True)
    event = JobLifecycleEvent(
        job_id=result.job_id,
        metric=snapshot.metric,
        timestamp=now,
        status=result.status,
        error=getattr(result, "error", None),
    )
    encoded_event = _encode_event(event)
    ttl = _job_store_ttl_seconds()
    saved = client.eval(
        _SAVE_TERMINAL_RESULT_IF_ACTIVE_SCRIPT,
        6,
        status_key(result.job_id),
        result_key(result.job_id),
        events_key(result.job_id),
        provenance_key(result.job_id),
        job_idempotency_key(result.job_id),
        job_index_key(),
        _dump_json(result_payload),
        _dump_json(status_payload),
        event.kind,
        encoded_event,
        ttl,
        now.timestamp(),
        result.job_id,
        now.timestamp() - ttl,
        get_config().job_events.max_stream_events,
    )
    was_saved = bool(saved)
    if was_saved:
        _log_job_event(event)
    return was_saved


def get_job_result(
    job_id: str,
    client: SyncKeyReader | None = None,
) -> dict[str, Any] | None:
    client = _default_sync_client(client)
    payload = client.get(result_key(job_id))
    if payload is None:
        return None
    decoded = _loads_json(payload)
    if not isinstance(decoded, dict):
        msg = f"Stored result for job {job_id!r} is not a JSON object"
        raise TypeError(msg)
    return decoded


def get_job_provenance(
    job_id: str,
    client: SyncKeyReader | None = None,
) -> JobRunProvenance | None:
    client = _default_sync_client(client)
    payload = client.get(provenance_key(job_id))
    if payload is None:
        return None
    return JobRunProvenance.model_validate(_loads_json(payload))


def get_job_result_descriptor(
    job_id: str,
    *,
    client: SyncKeyReader | None = None,
) -> ResultDescriptor | None:
    client = _default_sync_client(client)
    payload = get_job_result(job_id, client=client)
    if payload is None:
        return None
    snapshot = get_job_status(job_id, client=client)
    if snapshot is None or not is_terminal_status(snapshot.status):
        return None
    return build_result_descriptor(
        parse_job_result(payload),
        completed_at=snapshot.updated_at,
        provenance=get_job_provenance(job_id, client=client),
        lifetime=get_result_lifetime(job_id, client=client),
    )


def get_job_status(
    job_id: str,
    client: SyncKeyReader | None = None,
) -> JobStatusSnapshot | None:
    client = _default_sync_client(client)
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
    client: SyncJobListClient | None = None,
) -> list[JobStatusSnapshot]:
    client = _default_sync_client(client)
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
    client: SyncAtomicJobWriter | None = None,
) -> tuple[JobStatusSnapshot | None, bool]:
    client = _default_sync_client(client)
    snapshot = get_job_status(job_id, client=client)
    if snapshot is None:
        return None, False
    if is_terminal_status(snapshot.status):
        return snapshot, False
    try:
        cancelled = set_job_status(
            job_id,
            "cancelled",
            metric=snapshot.metric,
            client=client,
        )
    except (JobCancelledError, RuntimeError):
        latest = get_job_status(job_id, client=client)
        if latest is not None and is_terminal_status(latest.status):
            return latest, False
        raise
    return cancelled, True


def is_job_cancelled(job_id: str, client: SyncKeyReader | None = None) -> bool:
    snapshot = get_job_status(job_id, client)
    return snapshot is not None and snapshot.status == "cancelled"


def raise_if_cancelled(job_id: str, client: SyncKeyReader | None = None) -> None:
    if is_job_cancelled(job_id, client):
        raise JobCancelledError(job_id)


def append_job_progress(
    event: JobProgressEvent,
    *,
    client: SyncAtomicJobWriter | None = None,
) -> StoredJobEvent:
    client = _default_sync_client(client)
    current = get_job_status(event.job_id, client=client)
    if current is None or current.status != "running":
        if current is not None and current.status == "cancelled":
            raise JobCancelledError(event.job_id)
        msg = f"Job {event.job_id!r} is not running."
        raise RuntimeError(msg)
    if current.progress is not None and current.progress.stage == event.stage:
        if event.current < current.progress.current:
            msg = f"Progress for stage {event.stage!r} must not decrease."
            raise ValueError(msg)
        if current.progress.total is not None and event.total != current.progress.total:
            msg = f"Progress total for stage {event.stage!r} must remain stable."
            raise ValueError(msg)
        if current.progress.unit != event.unit:
            msg = f"Progress unit for stage {event.stage!r} must remain stable."
            raise ValueError(msg)
    progress_level = (
        logging.INFO
        if current.progress is None
        or current.progress.stage != event.stage
        or (event.total is not None and event.current == event.total)
        else logging.DEBUG
    )
    snapshot = current.model_copy(
        update={"updated_at": event.timestamp, "progress": event.snapshot()}
    )
    stored_event = _append_event_and_snapshot_sync(
        snapshot,
        event,
        guard="running",
        client=client,
    )
    if stored_event is None:
        latest = get_job_status(event.job_id, client=client)
        if latest is not None and latest.status == "cancelled":
            raise JobCancelledError(event.job_id)
        msg = f"Job {event.job_id!r} stopped running before progress was stored."
        raise RuntimeError(msg)
    _log_job_event(event, progress_level=progress_level)
    return stored_event


def append_job_message(
    event: JobMessageEvent,
    *,
    client: SyncAtomicJobWriter | None = None,
) -> StoredJobEvent:
    client = _default_sync_client(client)
    current = get_job_status(event.job_id, client=client)
    if current is None or current.status != "running":
        if current is not None and current.status == "cancelled":
            raise JobCancelledError(event.job_id)
        msg = f"Job {event.job_id!r} is not running."
        raise RuntimeError(msg)
    snapshot = current.model_copy(
        update={"updated_at": event.timestamp, "latest_message": event.snapshot()}
    )
    stored_event = _append_event_and_snapshot_sync(
        snapshot,
        event,
        guard="running",
        client=client,
    )
    if stored_event is None:
        latest = get_job_status(event.job_id, client=client)
        if latest is not None and latest.status == "cancelled":
            raise JobCancelledError(event.job_id)
        msg = f"Job {event.job_id!r} stopped running before its message was stored."
        raise RuntimeError(msg)
    _log_job_event(event)
    return stored_event


def read_job_events(
    job_id: str,
    *,
    after_id: str | None = None,
    count: int | None = None,
    client: SyncEventReader | None = None,
) -> list[StoredJobEvent]:
    client = _default_sync_client(client)
    start_id = STREAM_START if after_id is None else f"({after_id}"
    records = client.xrange(events_key(job_id), start_id, count=count) or []
    return [_stored_event_from_record(record) for record in records]


async def create_job_async(
    job: JobEnvelope,
    provenance: JobRunProvenance | None = None,
    client: AsyncAtomicJobWriter | None = None,
) -> JobStatusSnapshot:
    client = _default_async_client(client)
    if provenance is not None:
        await _save_job_provenance_async(job.job_id, provenance, client=client)
    return await set_job_status_async(
        job.job_id,
        "queued",
        metric=job.metric,
        client=client,
    )


async def set_job_status_async(
    job_id: str,
    status: JobStatus,
    **options: Unpack[AsyncJobStatusOptions],
) -> JobStatusSnapshot:
    metric = options.get("metric")
    error = options.get("error")
    client = options.get("client")
    client = _default_async_client(client)
    current = await get_job_status_async(job_id, client=client)
    snapshot = _build_status_snapshot(
        job_id,
        status,
        previous=current,
        metric=metric,
        error=error,
    )
    event = JobLifecycleEvent(
        job_id=job_id,
        metric=snapshot.metric,
        timestamp=snapshot.updated_at,
        status=status,
        error=error,
    )
    guard = (
        "missing"
        if status == "queued"
        else "queued"
        if status == "running"
        else "active"
    )
    stored = await _append_event_and_snapshot_async(
        snapshot,
        event,
        guard=guard,
        client=client,
    )
    if stored is None:
        latest = await get_job_status_async(job_id, client=client)
        if latest is not None and latest.status == "cancelled":
            raise JobCancelledError(job_id)
        msg = f"Invalid job lifecycle transition for {job_id!r} to {status!r}."
        raise RuntimeError(msg)
    _log_job_event(event)
    return snapshot


async def get_job_status_async(
    job_id: str,
    client: AsyncKeyReader | None = None,
) -> JobStatusSnapshot | None:
    client = _default_async_client(client)
    payload = await client.get(status_key(job_id))
    if payload is None:
        return None
    return JobStatusSnapshot.model_validate(_loads_json(payload))


async def get_job_result_async(
    job_id: str,
    client: AsyncKeyReader | None = None,
) -> dict[str, Any] | None:
    client = _default_async_client(client)
    payload = await client.get(result_key(job_id))
    if payload is None:
        return None
    decoded = _loads_json(payload)
    if not isinstance(decoded, dict):
        msg = f"Stored result for job {job_id!r} is not a JSON object"
        raise TypeError(msg)
    return decoded


async def get_job_provenance_async(
    job_id: str,
    client: AsyncKeyReader | None = None,
) -> JobRunProvenance | None:
    client = _default_async_client(client)
    payload = await client.get(provenance_key(job_id))
    if payload is None:
        return None
    return JobRunProvenance.model_validate(_loads_json(payload))


async def get_job_result_descriptor_async(
    job_id: str,
    *,
    client: AsyncKeyReader | None = None,
) -> ResultDescriptor | None:
    client = _default_async_client(client)
    payload = await get_job_result_async(job_id, client=client)
    if payload is None:
        return None
    snapshot = await get_job_status_async(job_id, client=client)
    if snapshot is None or not is_terminal_status(snapshot.status):
        return None
    return build_result_descriptor(
        parse_job_result(payload),
        completed_at=snapshot.updated_at,
        provenance=await get_job_provenance_async(job_id, client=client),
        lifetime=await get_result_lifetime_async(job_id, client=client),
    )


async def delete_job_result_async(
    job_id: str,
    client: AsyncDeleteClient | None = None,
) -> None:
    client = _default_async_client(client)
    await client.delete(result_key(job_id))


async def read_job_events_async(
    job_id: str,
    *,
    after_id: str | None = None,
    count: int | None = None,
    client: AsyncEventReader | None = None,
) -> list[StoredJobEvent]:
    client = _default_async_client(client)
    start_id = STREAM_START if after_id is None else f"({after_id}"
    records = await client.xrange(events_key(job_id), start_id, count=count) or []
    return [_stored_event_from_record(record) for record in records]


async def read_new_job_events_async(
    job_id: str,
    *,
    after_id: str = STREAM_LATEST,
    block_ms: int = DEFAULT_STREAM_BLOCK_MS,
    count: int | None = None,
    client: AsyncNewEventReader | None = None,
) -> list[StoredJobEvent]:
    client = _default_async_client(client)
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


def _stored_event_from_record(record: RedisStreamRecord) -> StoredJobEvent:
    stream_id, fields = record
    if fields is None:
        msg = f"Redis stream record {stream_id!r} did not include fields."
        raise ValueError(msg)
    if isinstance(stream_id, bytes):
        stream_id = stream_id.decode()

    payload = fields.get("payload")
    if payload is None:
        payload = fields.get(b"payload")
    if payload is None:
        msg = f"Redis stream record {stream_id!r} did not include a payload."
        raise ValueError(msg)

    return StoredJobEvent(
        stream_id=str(stream_id),
        event=parse_job_event(_loads_json(payload)),
    )


__all__ = [
    "DEFAULT_AGENT_SCOPE",
    "JOB_INDEX_KEY",
    "JOB_STORE_TTL_SECONDS",
    "STREAM_LATEST",
    "STREAM_START",
    "TERMINAL_STATUSES",
    "IdempotencyRecord",
    "JobCancelledError",
    "JobStatus",
    "JobStatusSnapshot",
    "StoredJobEvent",
    "TerminalJobStatus",
    "append_job_message",
    "append_job_progress",
    "cancel_job",
    "claim_idempotency_key_async",
    "create_job",
    "create_job_async",
    "delete_job_result_async",
    "events_key",
    "get_job_provenance",
    "get_job_provenance_async",
    "get_job_result",
    "get_job_result_async",
    "get_job_result_descriptor",
    "get_job_result_descriptor_async",
    "get_job_status",
    "get_job_status_async",
    "get_result_lifetime",
    "get_result_lifetime_async",
    "idempotency_key",
    "is_job_cancelled",
    "is_terminal_status",
    "job_idempotency_key",
    "job_index_key",
    "list_job_statuses",
    "provenance_key",
    "raise_if_cancelled",
    "read_job_events",
    "read_job_events_async",
    "read_new_job_events_async",
    "release_idempotency_key_async",
    "result_key",
    "save_job_result",
    "save_job_result_if_active",
    "set_job_status",
    "set_job_status_async",
    "status_key",
]
