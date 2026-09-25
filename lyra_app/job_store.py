"""Small JSON-only adapter over native RQ jobs and registries."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lyra.sdk.models.job import (
    AdminJobDetail,
    FailedJobResult,
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
from pydantic import ValidationError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus
from rq.registry import FailedJobRegistry, FinishedJobRegistry, StartedJobRegistry
from rq.serializers import JSONSerializer

from lyra_app.config import get_config
from lyra_app.db.redis import get_sync_client

if TYPE_CHECKING:
    from lyra.sdk.models.job import JobEnvelope
    from lyra.sdk.types import JsonObject

    from lyra_app.registry import MetricRegistryEntry


@dataclass(frozen=True)
class JobObservation:
    """Best-effort retained observation; lifecycle always comes from RQ."""

    snapshot: JobStatusInfo | None = None
    result: TerminalJobResult | None = None
    provenance: JobRunProvenance | None = None
    lifetime: ResultLifetime = field(default_factory=ResultLifetime)


def enqueue(
    envelope: JobEnvelope, provenance: JobRunProvenance, entry: MetricRegistryEntry
) -> None:
    """Enqueue once, without retry, queue-age expiry, or deduplication."""
    settings = get_config().jobs
    queue = Queue(entry.queue, connection=get_sync_client(), serializer=JSONSerializer)
    queue.enqueue_call(
        "lyra_app.worker.run_metric_task",
        args=(envelope.model_dump(mode="json"),),
        job_id=envelope.job_id,
        timeout=settings.execution_timeout_seconds,
        result_ttl=settings.result_retention_seconds,
        failure_ttl=settings.result_retention_seconds,
        meta={
            "provenance": provenance.model_dump(mode="json"),
            "contract": entry.metric.model_dump(mode="json"),
            "distribution": entry.distribution,
        },
    )


def _observation(job: Job) -> JobObservation:
    status = job.get_status(refresh=False)
    states: dict[JobStatus, JobLifecycleStatus] = {
        JobStatus.QUEUED: "queued",
        JobStatus.STARTED: "running",
        JobStatus.FINISHED: "succeeded",
        JobStatus.FAILED: "failed",
    }
    if status not in states:
        return JobObservation()
    result = None
    if status == JobStatus.FINISHED:
        record = job.latest_result()
        if record is None:
            return JobObservation()
        result = parse_job_result(record.return_value)
    elif status == JobStatus.FAILED:
        try:
            result = FailedJobResult.model_validate(job.meta.get("failure"))
        except ValidationError:
            result = FailedJobResult(
                job_id=job.id,
                error={"type": "worker", "message": "Job execution failed."},
            )
    provenance = None
    progress = None
    with suppress(ValidationError):
        provenance = JobRunProvenance.model_validate(job.meta.get("provenance"))
    with suppress(ValidationError):
        progress = JobProgress.model_validate(job.meta.get("progress"))
    ttl = get_sync_client().ttl(job.key)
    if ttl == -2:
        return JobObservation()
    return JobObservation(
        snapshot=JobStatusInfo(
            job_id=job.id,
            status=states[status],
            created_at=job.created_at,
            updated_at=job.ended_at or job.started_at or job.created_at,
            started_at=job.started_at,
            completed_at=job.ended_at,
            metric=provenance.metric if provenance else None,
            error=result.error if isinstance(result, FailedJobResult) else None,
            progress=progress,
        ),
        result=result,
        provenance=provenance,
        lifetime=ResultLifetime(expires_in_seconds=ttl if ttl >= 0 else None),
    )


def read_job(job_id: str) -> JobObservation:
    """Read native storage without cleanup or record repair.

    Returns:
        Empty state for an absent or expired job.
    """
    try:
        return _observation(
            Job.fetch(job_id, connection=get_sync_client(), serializer=JSONSerializer)
        )
    except NoSuchJobError:
        return JobObservation()


async def observe_job(job_id: str) -> JobObservation:
    """Offload synchronous RQ reads from an asynchronous consumer.

    Returns:
        The currently retained observation.
    """
    return await asyncio.to_thread(read_job, job_id)


async def get_job_status_async(job_id: str) -> JobStatusInfo | None:
    """Return native lifecycle and best-effort progress."""
    return (await observe_job(job_id)).snapshot


async def get_job_result_async(job_id: str) -> JsonObject | None:
    """Return a retained validated terminal payload."""
    result = (await observe_job(job_id)).result
    return result.model_dump(mode="json") if result else None


async def get_job_result_descriptor_async(job_id: str) -> ResultDescriptor | None:
    """Return a retained result descriptor with captured provenance."""
    observation = await observe_job(job_id)
    if observation.result is None or observation.snapshot is None:
        return None
    return build_result_descriptor(
        observation.result,
        completed_at=observation.snapshot.completed_at
        or observation.snapshot.updated_at,
        provenance=observation.provenance,
        lifetime=observation.lifetime,
    )


def is_terminal_status(status: JobLifecycleStatus) -> bool:
    """Return whether native execution has finished."""
    return status in {"succeeded", "failed"}


def list_job_statuses(
    *, queue: str, status: JobLifecycleStatus, offset: int = 0, limit: int = 50
) -> list[JobStatusInfo]:
    """Read a queue or registry page without triggering worker maintenance.

    Returns:
        Still-retained matching jobs; transitions may shorten a page.
    """
    connection = get_sync_client()
    if status == "queued":
        ids = Queue(
            queue, connection=connection, serializer=JSONSerializer
        ).get_job_ids(offset=offset, length=limit)
    else:
        registry_class = {
            "running": StartedJobRegistry,
            "succeeded": FinishedJobRegistry,
            "failed": FailedJobRegistry,
        }[status]
        registry = registry_class(
            queue, connection=connection, serializer=JSONSerializer
        )
        ids = registry.get_job_ids(
            start=offset,
            end=offset + limit - 1,
            desc=status in {"succeeded", "failed"},
            cleanup=False,
        )
    snapshots = [read_job(job_id).snapshot for job_id in ids]
    return [item for item in snapshots if item is not None and item.status == status]


def get_job_detail(job_id: str) -> AdminJobDetail | None:
    """Read administrator-only native execution diagnostics.

    Returns:
        A retained job's status, queue, worker identity, and traceback.
    """
    try:
        job = Job.fetch(job_id, connection=get_sync_client(), serializer=JSONSerializer)
        observation = _observation(job)
        if observation.snapshot is None:
            return None
        return AdminJobDetail(
            snapshot=observation.snapshot,
            queue=job.origin,
            worker_id=job.worker_name or job.meta.get("worker_id"),
            failure_diagnostics=(
                record.exc_string if (record := job.latest_result()) else None
            ),
        )
    except NoSuchJobError:
        return None
