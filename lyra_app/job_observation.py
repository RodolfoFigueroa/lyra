"""Atomic retained job observations shared by application consumers."""

import json
from dataclasses import dataclass, field

from lyra.sdk.models.job import (
    JobRunProvenance,
    JobStatusInfo,
    ResultLifetime,
    TerminalJobResult,
    parse_job_result,
)

from lyra_app import job_store
from lyra_app.db.redis import redis_client
from lyra_app.worker_control import repair_celery_failure


@dataclass(frozen=True)
class JobObservation:
    """One coherent snapshot of retained state, payload, provenance, and lifetime."""

    snapshot: JobStatusInfo | None = None
    result: TerminalJobResult | None = None
    provenance: JobRunProvenance | None = None
    lifetime: ResultLifetime = field(default_factory=ResultLifetime)


async def read_job_observation(job_id: str) -> JobObservation:
    """Read all retained result keys together in a Redis transaction.

    Returns:
        A typed observation, including empty state for an unknown reference.
    """
    async with redis_client.pipeline(transaction=True) as pipeline:
        pipeline.get(job_store.status_key(job_id))
        pipeline.get(job_store.result_key(job_id))
        pipeline.get(job_store.provenance_key(job_id))
        pipeline.pttl(job_store.result_key(job_id))
        status, result, provenance, ttl = await pipeline.execute()
    return JobObservation(
        snapshot=JobStatusInfo.model_validate_json(status) if status else None,
        result=parse_job_result(json.loads(result)) if result else None,
        provenance=JobRunProvenance.model_validate_json(provenance)
        if provenance
        else None,
        lifetime=job_store.lifetime_from_ttl_ms(ttl),
    )


async def observe_job(job_id: str) -> JobObservation:
    """Observe once, with at most one reread after a Celery failure repair.

    Returns:
        Retained results take precedence over stale or missing lifecycle state.
    """
    observation = await read_job_observation(job_id)
    if (
        observation.result is None
        and observation.snapshot is not None
        and await repair_celery_failure(observation.snapshot)
    ):
        return await read_job_observation(job_id)
    return observation
