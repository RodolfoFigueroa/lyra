"""Persistent job-state access and transition operations."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    runtime_checkable,
)

from lyra.sdk.models.job import (
    CancelledJobResult,
    JobEnvelope,
    JobLifecycleStatus,
    JobProgress,
    JobRunProvenance,
    JobStatusInfo,
    ResultDescriptor,
    ResultLifetime,
    TerminalJobResult,
    build_result_descriptor,
    parse_job_result,
)
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field

from lyra_app.config import (
    LyraConfig,
    get_config,
)
from lyra_app.db.redis import redis_client, redis_client_sync

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from lyra.sdk.types import JsonValue

JobStatus: TypeAlias = JobLifecycleStatus

TerminalJobStatus: TypeAlias = Literal["succeeded", "failed", "cancelled"]


JobStatusSnapshot = JobStatusInfo
JOB_INDEX_KEY = "jobs:index"
TERMINAL_STATUSES: set[TerminalJobStatus] = {"succeeded", "failed", "cancelled"}
DEFAULT_AGENT_SCOPE = "shared-agent"
AGENT_SUBMISSION_LIMIT_KEY = "jobs:submission-limit:shared-agent"
logger = logging.getLogger(__name__)

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

_RELEASE_IDEMPOTENCY_SCRIPT = """
local reservation = cjson.decode(ARGV[1])
if redis.call('exists', 'job:' .. reservation['job_id'] .. ':status') == 1 then
    return 0
end
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
""".strip()

RedisPayload: TypeAlias = str | bytes
RedisScriptResult: TypeAlias = RedisPayload | int | list[int]


@runtime_checkable
class SyncKeyReader(Protocol):
    """Read a Redis key synchronously."""

    def get(self, key: str) -> RedisPayload | None:
        """Return the value stored at the key, if present."""
        ...


@runtime_checkable
class AsyncKeyReader(Protocol):
    """Read a Redis key asynchronously."""

    def get(self, key: str) -> Awaitable[RedisPayload | None]:
        """Return the value stored at the key, if present."""
        ...


@runtime_checkable
class SyncMillisecondLifetimeReader(Protocol):
    """Read a key lifetime in milliseconds synchronously."""

    def pttl(self, key: str) -> int:
        """Return the key lifetime in milliseconds using Redis sentinel values."""
        ...


@runtime_checkable
class SyncSecondLifetimeReader(Protocol):
    """Read a key lifetime in seconds synchronously."""

    def ttl(self, key: str) -> int:
        """Return the key lifetime in seconds using Redis sentinel values."""
        ...


@runtime_checkable
class AsyncMillisecondLifetimeReader(Protocol):
    """Read a key lifetime in milliseconds asynchronously."""

    def pttl(self, key: str) -> Awaitable[int]:
        """Return the key lifetime in milliseconds using Redis sentinel values."""
        ...


@runtime_checkable
class AsyncSecondLifetimeReader(Protocol):
    """Read a key lifetime in seconds asynchronously."""

    def ttl(self, key: str) -> Awaitable[int]:
        """Return the key lifetime in seconds using Redis sentinel values."""
        ...


class SyncAtomicJobWriter(SyncKeyReader, Protocol):
    """Read and write job state atomically using Redis scripts."""

    def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        /,
        *keys_and_args: str | float,
    ) -> RedisScriptResult:
        """Execute a Redis script with keys and arguments."""
        ...


class AsyncAtomicJobWriter(AsyncKeyReader, Protocol):
    """Read and write job state atomically with awaitable scripts."""

    def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        /,
        *keys_and_args: str | float,
    ) -> Awaitable[RedisScriptResult]:
        """Execute a Redis script with keys and arguments."""
        ...


class SyncJobClient(SyncKeyReader, Protocol):
    """Read and persist job state synchronously."""


class SyncConditionalJobWriter(SyncJobClient, Protocol):
    """Apply conditional job transitions using Redis scripts."""

    def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        /,
        *keys_and_args: str | float,
    ) -> RedisScriptResult:
        """Execute a Redis script with keys and arguments."""
        ...


class SyncJobListClient(SyncKeyReader, Protocol):
    """Read and prune the sorted job index."""

    def zrevrange(self, key: str, start: int, stop: int) -> Sequence[RedisPayload]:
        """Return sorted-set members in descending score order."""
        ...

    def zrem(self, key: str, *members: str) -> int | None:
        """Remove the named members from a sorted set."""
        ...


class AsyncDeleteClient(Protocol):
    """Delete Redis keys asynchronously."""

    def delete(self, key: str) -> Awaitable[int | None]:
        """Remove a key and return the number of keys deleted."""
        ...


class AsyncScriptClient(Protocol):
    """Execute Redis scripts asynchronously."""

    def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        /,
        *args: str | float,
    ) -> Awaitable[RedisScriptResult]:
        """Execute a Redis script with keys and arguments."""
        ...


class AsyncIdempotencyClient(
    AsyncKeyReader, AsyncDeleteClient, AsyncScriptClient, Protocol
):
    """Store and release idempotency records atomically."""

    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool = False,
    ) -> Awaitable[bool | None]:
        """Store a value with expiration and optional create-only semantics."""
        ...


RedisClientT = TypeVar("RedisClientT")


def _default_sync_client(client: RedisClientT | None) -> RedisClientT:
    if client is not None:
        return client
    return cast("RedisClientT", redis_client_sync)


def _default_async_client(client: RedisClientT | None) -> RedisClientT:
    if client is not None:
        return client
    return cast("RedisClientT", redis_client)


class IdempotencyRecord(StrictBaseModel):
    """Bind a caller request digest to the job created for that request."""

    request_digest: str = Field(min_length=1)
    job_id: str = Field(min_length=1)


class AgentSubmissionLimitDecision(StrictBaseModel):
    """Report whether a submission fits the agent rate limit."""

    accepted: bool
    count: int = Field(ge=0)
    retry_after_seconds: int = Field(ge=1)


class JobCancelledError(RuntimeError):
    """Indicate that an operation cannot continue because its job was cancelled."""

    def __init__(self, job_id: str) -> None:
        """Initialize the error for a cancelled job identifier."""
        super().__init__(f"Job {job_id!r} was cancelled.")
        self.job_id = job_id


def status_key(job_id: str) -> str:
    """Return the Redis key containing a job's status snapshot."""
    return f"job:{job_id}:status"


def result_key(job_id: str) -> str:
    """Return the Redis key containing a job's terminal result."""
    return f"job:{job_id}:result"


def provenance_key(job_id: str) -> str:
    """Return the Redis key containing a job's execution provenance."""
    return f"job:{job_id}:provenance"


def idempotency_key(
    caller_key: str,
    *,
    agent_scope: str = DEFAULT_AGENT_SCOPE,
) -> str:
    """Derive a non-secret Redis reservation key from caller key and scope.

    Returns:
        A namespaced key containing the SHA-256 digest of the scope and caller key.
    """
    digest = hashlib.sha256(
        f"{agent_scope}\0{caller_key}".encode(),
    ).hexdigest()
    return f"jobs:idempotency:{digest}"


def job_idempotency_key(job_id: str) -> str:
    """Return the Redis key linking a job to its idempotency reservation."""
    return f"job:{job_id}:idempotency"


def job_index_key() -> str:
    """Return the Redis sorted-set key indexing retained jobs."""
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
    """Atomically consume capacity from the shared agent fixed window.

    Returns:
        Whether the request was accepted, the current count, and retry delay.

    Raises:
        RuntimeError: If Redis returns a malformed script response.
    """
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


def _json_non_finite_constant(_: str) -> None:
    return None


def _loads_json(payload: RedisPayload) -> JsonValue:
    if isinstance(payload, bytes):
        payload = payload.decode()
    return json.loads(payload, parse_constant=_json_non_finite_constant)


def _result_retention_seconds(config: LyraConfig | None = None) -> int:
    if config is not None:
        return config.job_store.result_retention_seconds
    return get_config().job_store.result_retention_seconds


def lifetime_from_ttl_ms(ttl_ms: int | None) -> ResultLifetime:
    """Return available expiration metadata from a Redis millisecond TTL."""
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
    """Read the remaining retention lifetime of a synchronous job result.

    Returns:
        Available expiration metadata, or an empty lifetime when unsupported.
    """
    client = _default_sync_client(client)
    key = result_key(job_id)
    if isinstance(client, SyncMillisecondLifetimeReader) and callable(client.pttl):
        return lifetime_from_ttl_ms(client.pttl(key))
    if isinstance(client, SyncSecondLifetimeReader) and callable(client.ttl):
        return _lifetime_from_ttl_seconds(client.ttl(key))
    return ResultLifetime()


async def get_result_lifetime_async(
    job_id: str,
    *,
    client: AsyncKeyReader | None = None,
) -> ResultLifetime:
    """Read the remaining retention lifetime of an asynchronous job result.

    Returns:
        Available expiration metadata, or an empty lifetime when unsupported.
    """
    client = _default_async_client(client)
    key = result_key(job_id)
    if isinstance(client, AsyncMillisecondLifetimeReader):
        return lifetime_from_ttl_ms(await client.pttl(key))
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
    """Atomically bind one caller key to a request digest and job identity.

    Returns:
        The stored reservation and whether this call acquired it.
    """
    client = _default_async_client(client)
    key = idempotency_key(caller_key, agent_scope=agent_scope)
    record = IdempotencyRecord(request_digest=request_digest, job_id=job_id)
    encoded = _dump_json(record.model_dump(mode="json"))
    ttl = _result_retention_seconds()
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
    """Release only the exact reservation owned by ``record``.

    Returns:
        ``True`` if the matching reservation was removed, otherwise ``False``.
    """
    client = _default_async_client(client)
    key = idempotency_key(caller_key, agent_scope=agent_scope)
    encoded = _dump_json(record.model_dump(mode="json"))
    released = await client.eval(_RELEASE_IDEMPOTENCY_SCRIPT, 1, key, encoded)
    if released:
        await client.delete(job_idempotency_key(record.job_id))
    return bool(released)


def _decode_job_index_member(member: RedisPayload) -> str:
    if isinstance(member, bytes):
        return member.decode()
    return str(member)


def get_job_result(
    job_id: str,
    client: SyncKeyReader | None = None,
) -> dict[str, Any] | None:
    """Return a decoded terminal job result when one is retained.

    Returns:
        The decoded result object, or ``None`` when no result is retained.

    Raises:
        TypeError: If the stored JSON value is not an object.
    """
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
    """Return the execution provenance retained for a job."""
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
    """Build a terminal result descriptor with provenance and lifetime.

    Returns:
        A descriptor for a retained terminal result, or ``None`` if unavailable.
    """
    client = _default_sync_client(client)
    payload = get_job_result(job_id, client=client)
    if payload is None:
        return None
    snapshot = get_job_status(job_id, client=client)
    if snapshot is None or not is_terminal_status(snapshot.status):
        return None
    return build_result_descriptor(
        parse_job_result(payload),
        completed_at=snapshot.completed_at or snapshot.updated_at,
        provenance=get_job_provenance(job_id, client=client),
        lifetime=get_result_lifetime(job_id, client=client),
    )


def get_job_status(
    job_id: str,
    client: SyncKeyReader | None = None,
) -> JobStatusSnapshot | None:
    """Return the latest retained status snapshot for a job."""
    client = _default_sync_client(client)
    payload = client.get(status_key(job_id))
    if payload is None:
        return None
    return JobStatusSnapshot.model_validate(_loads_json(payload))


def is_terminal_status(status: JobStatus) -> bool:
    """Return whether a lifecycle status prevents further transitions."""
    return status in TERMINAL_STATUSES


def list_job_statuses(
    *,
    limit: int = 50,
    status: JobStatus | None = None,
    metric: str | None = None,
    client: SyncJobListClient | None = None,
) -> list[JobStatusSnapshot]:
    """List recent retained job snapshots with optional status and metric filters.

    Returns:
        Up to ``limit`` matching snapshots in reverse chronological order.
    """
    client = _default_sync_client(client)
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


def is_job_cancelled(job_id: str, client: SyncKeyReader | None = None) -> bool:
    """Return whether execution should stop for cancelled or missing state."""
    snapshot = get_job_status(job_id, client)
    return snapshot is None or snapshot.status == "cancelled"


def raise_if_cancelled(job_id: str, client: SyncKeyReader | None = None) -> None:
    """Raise ``JobCancelledError`` for cancelled or missing execution state.

    Raises:
        JobCancelledError: If the retained job status is ``cancelled``.
    """
    if is_job_cancelled(job_id, client):
        raise JobCancelledError(job_id)


async def get_job_status_async(
    job_id: str,
    client: AsyncKeyReader | None = None,
) -> JobStatusSnapshot | None:
    """Asynchronously return the latest retained status snapshot for a job.

    Returns:
        The decoded status snapshot, or ``None`` when it is not retained.
    """
    client = _default_async_client(client)
    payload = await client.get(status_key(job_id))
    if payload is None:
        return None
    return JobStatusSnapshot.model_validate(_loads_json(payload))


async def get_job_result_async(
    job_id: str,
    client: AsyncKeyReader | None = None,
) -> dict[str, Any] | None:
    """Asynchronously return a decoded terminal result when retained.

    Returns:
        The decoded result object, or ``None`` when no result is retained.

    Raises:
        TypeError: If the stored JSON value is not an object.
    """
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
    """Asynchronously return the execution provenance retained for a job.

    Returns:
        The decoded provenance, or ``None`` when it is not retained.
    """
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
    """Asynchronously build a terminal result descriptor and retention metadata.

    Returns:
        A descriptor for a retained terminal result, or ``None`` if unavailable.
    """
    client = _default_async_client(client)
    payload = await get_job_result_async(job_id, client=client)
    if payload is None:
        return None
    snapshot = await get_job_status_async(job_id, client=client)
    if snapshot is None or not is_terminal_status(snapshot.status):
        return None
    return build_result_descriptor(
        parse_job_result(payload),
        completed_at=snapshot.completed_at or snapshot.updated_at,
        provenance=await get_job_provenance_async(job_id, client=client),
        lifetime=await get_result_lifetime_async(job_id, client=client),
    )


async def delete_job_result_async(
    job_id: str,
    client: AsyncDeleteClient | None = None,
) -> None:
    """Delete a retained terminal result without removing other job state."""
    client = _default_async_client(client)
    await client.delete(result_key(job_id))


_CREATE_JOB_SCRIPT = """
-- create-job
if redis.call('exists', KEYS[1]) == 1 then return 0 end
local binding = redis.call('get', KEYS[3])
if ARGV[4] == 'reserved' then
    if not binding then return 0 end
    local reservation = redis.call('get', binding)
    if not reservation or cjson.decode(reservation)['job_id'] ~= ARGV[3] then
        return 0
    end
    redis.call('persist', binding)
    redis.call('persist', KEYS[3])
end
redis.call('set', KEYS[1], ARGV[1])
if ARGV[2] ~= '' then redis.call('set', KEYS[2], ARGV[2]) end
redis.call('zadd', KEYS[4], ARGV[5], ARGV[3])
return 1
""".strip()

_CLAIM_JOB_SCRIPT = """
-- claim-job
local raw = redis.call('get', KEYS[1])
if not raw then return 0 end
local current = cjson.decode(raw)
if current['status'] ~= 'queued' then return 0 end
current['status'] = 'running'
current['started_at'] = ARGV[1]
current['updated_at'] = ARGV[1]
redis.call('set', KEYS[1], cjson.encode(current))
return 1
""".strip()

_UPDATE_PROGRESS_SCRIPT = """
-- update-progress
local raw = redis.call('get', KEYS[1])
if not raw then return 0 end
local current = cjson.decode(raw)
if current['status'] ~= 'running' then return 0 end
current['progress'] = cjson.decode(ARGV[1])
current['updated_at'] = ARGV[2]
redis.call('set', KEYS[1], cjson.encode(current))
return 1
""".strip()

_SAVE_TERMINAL_RESULT_IF_ACTIVE_SCRIPT = """
-- complete-job
local raw = redis.call('get', KEYS[1])
if not raw then return 0 end
local current = cjson.decode(raw)
if current['status'] ~= 'queued' and current['status'] ~= 'running' then
    return 0
end
if ARGV[4] == 'queued' and current['status'] ~= 'queued' then return 0 end
local result = cjson.decode(ARGV[1])
current['status'] = result['status']
current['error'] = result['error']
current['completed_at'] = ARGV[2]
current['updated_at'] = ARGV[2]
redis.call('set', KEYS[1], cjson.encode(current))
redis.call('set', KEYS[2], ARGV[1])
local clock = redis.call('time')
local deadline = clock[1] * 1000 + math.floor(clock[2] / 1000) + ARGV[3] * 1000
for i = 1, 4 do redis.call('pexpireat', KEYS[i], deadline) end
local binding = redis.call('get', KEYS[4])
if binding then redis.call('pexpireat', binding, deadline) end
return 1
""".strip()


def _creation_args(
    job: JobEnvelope,
    provenance: JobRunProvenance | None,
) -> tuple[str | float, ...]:
    now = _now()
    snapshot = JobStatusSnapshot(
        job_id=job.job_id,
        metric=job.metric,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    return (
        provenance_key(job.job_id),
        job_idempotency_key(job.job_id),
        job_index_key(),
        snapshot.model_dump_json(),
        provenance.model_dump_json(exclude_none=True) if provenance else "",
        job.job_id,
        "reserved" if job.idempotency_key else "",
        now.timestamp(),
    )


def _log_transition(job_id: str, status: str, metric: str | None = None) -> None:
    logger.info(
        "Job lifecycle changed: %s",
        status,
        extra={
            "structured_fields": {
                "job_id": job_id,
                "metric": metric,
                "job_status": status,
            }
        },
    )


def create_job(
    job: JobEnvelope,
    provenance: JobRunProvenance | None = None,
    client: SyncAtomicJobWriter | None = None,
) -> JobStatusSnapshot:
    """Atomically accept a job and preserve its owned reservation.

    Returns:
        The accepted queued status.

    Raises:
        RuntimeError: If the job exists or reservation ownership was lost.
    """
    client = _default_sync_client(client)
    if not client.eval(
        _CREATE_JOB_SCRIPT, 4, status_key(job.job_id), *_creation_args(job, provenance)
    ):
        msg = "Job acceptance failed: existing job or lost reservation."
        raise RuntimeError(msg)
    _log_transition(job.job_id, "queued", job.metric)
    return cast("JobStatusSnapshot", get_job_status(job.job_id, client=client))


async def create_job_async(
    job: JobEnvelope,
    provenance: JobRunProvenance | None = None,
    client: AsyncAtomicJobWriter | None = None,
) -> JobStatusSnapshot:
    """Atomically accept a queued job without an active-state expiration.

    Returns:
        The accepted status snapshot.

    Raises:
        RuntimeError: If the job exists or reservation ownership was lost.
    """
    client = _default_async_client(client)
    if not await client.eval(
        _CREATE_JOB_SCRIPT, 4, status_key(job.job_id), *_creation_args(job, provenance)
    ):
        msg = "Job acceptance failed: existing job or lost reservation."
        raise RuntimeError(msg)
    _log_transition(job.job_id, "queued", job.metric)
    return cast(
        "JobStatusSnapshot", await get_job_status_async(job.job_id, client=client)
    )


def claim_job(job_id: str, *, client: SyncAtomicJobWriter | None = None) -> bool:
    """Claim a queued job exactly once before invoking its plugin.

    Returns:
        Whether this worker claimed execution.
    """
    client = _default_sync_client(client)
    claimed = bool(
        client.eval(_CLAIM_JOB_SCRIPT, 1, status_key(job_id), _now().isoformat())
    )
    if claimed:
        snapshot = get_job_status(job_id, client=client)
        _log_transition(job_id, "running", snapshot.metric if snapshot else None)
    return claimed


def update_job_progress(
    job_id: str,
    progress: JobProgress,
    *,
    client: SyncAtomicJobWriter | None = None,
) -> bool:
    """Write a snapshot only while the job is running.

    Returns:
        Whether the update was accepted.
    """
    client = _default_sync_client(client)
    return bool(
        client.eval(
            _UPDATE_PROGRESS_SCRIPT,
            1,
            status_key(job_id),
            progress.model_dump_json(),
            progress.timestamp.isoformat(),
        )
    )


def _completion_args(
    result: TerminalJobResult, *, queued_only: bool
) -> tuple[str | float, ...]:
    return (
        result_key(result.job_id),
        provenance_key(result.job_id),
        job_idempotency_key(result.job_id),
        result.model_dump_json(exclude_none=True),
        _now().isoformat(),
        _result_retention_seconds(),
        "queued" if queued_only else "active",
    )


def save_job_result_if_active(
    result: TerminalJobResult,
    *,
    client: SyncConditionalJobWriter | None = None,
) -> bool:
    """Persist result and terminal status together without renewing finished jobs.

    Returns:
        Whether the terminal transition was accepted.
    """
    client = _default_sync_client(client)
    saved = bool(
        client.eval(
            _SAVE_TERMINAL_RESULT_IF_ACTIVE_SCRIPT,
            4,
            status_key(result.job_id),
            *_completion_args(result, queued_only=False),
        )
    )
    if saved:
        snapshot = get_job_status(result.job_id, client=client)
        _log_transition(
            result.job_id, result.status, snapshot.metric if snapshot else None
        )
    return saved


async def fail_queued_job_async(
    result: TerminalJobResult,
    *,
    client: AsyncAtomicJobWriter | None = None,
) -> bool:
    """Record a dispatch failure only if execution has not been claimed.

    Returns:
        Whether the queued job was transitioned to failed.
    """
    client = _default_async_client(client)
    saved = bool(
        await client.eval(
            _SAVE_TERMINAL_RESULT_IF_ACTIVE_SCRIPT,
            4,
            status_key(result.job_id),
            *_completion_args(result, queued_only=True),
        )
    )
    if saved:
        _log_transition(result.job_id, result.status)
    return saved


def save_job_result(
    result: TerminalJobResult,
    *,
    client: SyncConditionalJobWriter | None = None,
) -> dict[str, Any]:
    """Persist a result if active, preserving any prior terminal result.

    Returns:
        The retained result, or the unpersisted input for missing jobs.
    """
    client = _default_sync_client(client)
    save_job_result_if_active(result, client=client)
    return get_job_result(result.job_id, client=client) or result.model_dump(
        mode="json", exclude_none=True
    )


def cancel_job(
    job_id: str,
    *,
    client: SyncAtomicJobWriter | None = None,
) -> tuple[JobStatusSnapshot | None, bool]:
    """Atomically cancel an active job and persist its terminal result.

    Returns:
        Current status and whether cancellation won the transition.
    """
    client = _default_sync_client(client)
    changed = save_job_result_if_active(
        CancelledJobResult(job_id=job_id), client=client
    )
    return get_job_status(job_id, client=client), changed
