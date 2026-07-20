from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from lyra.sdk.models import (
    JobCreateRequest,
    JobCreateResponse,
    JobEnvelope,
    JobLinks,
    JobRunProvenance,
    PluginInfoV3,
)
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.plugin_v3 import TableOutputV3
from lyra.utils.geometry import calculate_feature_areas_m2
from redis.exceptions import RedisError

from lyra_app import job_store
from lyra_app.config import get_config
from lyra_app.db.redis import redis_client
from lyra_app.registry import get_metric_entry, validate_metric_entry_payload
from lyra_app.spatial_inputs import (
    SpatialInputValidationError,
    resolve_spatial_inputs_with_metadata,
)

GENERIC_TASK_NAME = "lyra.run_metric"

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.sdk.models.plugin_v3 import SpatialInputKindV3

    from lyra_app.db.connection import ApplicationDatabaseRuntime
    from lyra_app.spatial_inputs import SpatialInputResolution


class TaskDispatcher(Protocol):
    def send_task(
        self,
        name: str,
        *,
        args: list[dict[str, Any]],
        queue: str,
        task_id: str,
    ) -> Any: ...


class UnknownMetricError(Exception):
    def __init__(self, metric: str) -> None:
        self.metric = metric
        super().__init__(f"Unknown metric: {metric}")


class SubmissionUnavailableError(Exception):
    def __init__(self) -> None:
        super().__init__("Cannot connect to Redis. Please try again later.")


class SubmissionRateLimitedError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Agent job submission limit exceeded. Please try again later.")

    @property
    def details(self) -> dict[str, int]:
        return {"retry_after_seconds": self.retry_after_seconds}


class IdempotencyConflictError(Exception):
    def __init__(self, *, idempotency_key: str, job_id: str) -> None:
        self.idempotency_key = idempotency_key
        self.job_id = job_id
        super().__init__("The idempotency key is already bound to a different request.")

    @property
    def details(self) -> dict[str, str]:
        return {
            "idempotency_key": self.idempotency_key,
            "job_id": self.job_id,
        }


def canonical_request_fingerprint(
    metric: str,
    request: Mapping[str, Any],
) -> str:
    """Digest a metric and validated unresolved request using canonical JSON."""

    encoded = json.dumps(
        {"metric": metric, "input": request},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def job_links(job_id: str) -> JobLinks:
    base = f"/jobs/{job_id}"
    return JobLinks(self=base, events=f"{base}/events", result=f"{base}/result")


async def _ensure_redis_available(client: Any) -> None:
    try:
        pong = await client.ping()
    except RedisError as exc:
        raise SubmissionUnavailableError from exc
    if not pong:
        raise SubmissionUnavailableError


def _new_job_id() -> str:
    return uuid4().hex


async def _release_failed_submission(
    *,
    caller_key: str | None,
    reservation: job_store.IdempotencyRecord | None,
    limit_consumed: bool,
    agent_scope: str,
    client: Any,
) -> None:
    try:
        if limit_consumed:
            await job_store.release_agent_submission_limit_async(client=client)
    finally:
        if reservation is not None and caller_key is not None:
            await job_store.release_idempotency_key_async(
                caller_key,
                reservation,
                agent_scope=agent_scope,
                client=client,
            )


async def _resolve_spatial_input(
    validated_input: dict[str, Any],
    spatial_inputs: dict[str, SpatialInputKindV3],
    database: ApplicationDatabaseRuntime | None,
) -> SpatialInputResolution:
    if database is None:
        return await asyncio.to_thread(
            resolve_spatial_inputs_with_metadata,
            validated_input,
            spatial_inputs,
        )

    from lyra_app.converters import build_converter_map  # noqa: PLC0415

    converter_map = build_converter_map(database.require_spatial_engine())
    return await database.run_spatial(
        resolve_spatial_inputs_with_metadata,
        validated_input,
        spatial_inputs,
        converter_map,
    )


def _requires_location_areas(output: Any) -> bool:
    return isinstance(output, TableOutputV3) and any(
        column.derivations for column in output.columns
    )


async def _calculate_location_areas(
    resolved_input: dict[str, Any],
) -> dict[str, float]:
    location = GeoJSON.model_validate(resolved_input["location"])
    try:
        return await asyncio.to_thread(calculate_feature_areas_m2, location)
    except ValueError as exc:
        raise SpatialInputValidationError(
            [{"loc": ["location"], "msg": str(exc), "type": "value_error"}]
        ) from exc


async def submit_job(
    request: JobCreateRequest,
    *,
    client: Any | None = None,
    dispatcher: TaskDispatcher | None = None,
    agent_scope: str = job_store.DEFAULT_AGENT_SCOPE,
    job_id_factory: Callable[[], str] | None = None,
    database: ApplicationDatabaseRuntime | None = None,
) -> JobCreateResponse:
    """Validate, deduplicate, persist, and dispatch one public job request."""

    if client is None:
        client = redis_client
    if dispatcher is None:
        from lyra_app.celery_app import celery_app  # noqa: PLC0415

        dispatcher = celery_app
    if job_id_factory is None:
        job_id_factory = _new_job_id

    await _ensure_redis_available(client)
    entry = get_metric_entry(request.metric)
    if entry is None:
        raise UnknownMetricError(request.metric)

    validated_input = validate_metric_entry_payload(entry, request.input)
    job_id = job_id_factory()
    reservation: job_store.IdempotencyRecord | None = None
    if request.idempotency_key is not None:
        request_digest = canonical_request_fingerprint(request.metric, validated_input)
        reservation, acquired = await job_store.claim_idempotency_key_async(
            request.idempotency_key,
            request_digest,
            job_id,
            agent_scope=agent_scope,
            client=client,
        )
        if not acquired:
            if reservation.request_digest != request_digest:
                raise IdempotencyConflictError(
                    idempotency_key=request.idempotency_key,
                    job_id=reservation.job_id,
                )
            return JobCreateResponse(
                job_id=reservation.job_id,
                metric=request.metric,
                status="queued",
                reused=True,
                links=job_links(reservation.job_id),
            )

    dispatched = False
    limit_consumed = False
    try:
        created_at = datetime.now(UTC)
        resolution = await _resolve_spatial_input(
            validated_input,
            entry.metric.spatial_inputs,
            database,
        )
        location_areas_m2 = None
        if _requires_location_areas(entry.metric.output):
            location_areas_m2 = await _calculate_location_areas(resolution.input)
        submission_limit = get_config().agent_submission_limit
        try:
            limit_decision = await job_store.consume_agent_submission_limit_async(
                limit=submission_limit.limit,
                window_seconds=submission_limit.window_seconds,
                client=client,
            )
        except RedisError as exc:
            raise SubmissionUnavailableError from exc
        if not limit_decision.accepted:
            raise SubmissionRateLimitedError(limit_decision.retry_after_seconds)
        limit_consumed = True
        envelope = JobEnvelope(
            job_id=job_id,
            metric=request.metric,
            input=resolution.input,
            idempotency_key=request.idempotency_key,
            location_areas_m2=location_areas_m2,
        )
        provenance = JobRunProvenance(
            metric=request.metric,
            catalog_fingerprint=entry.catalog_fingerprint,
            plugin=PluginInfoV3(
                name=entry.plugin_name,
                version=entry.plugin_version,
            ),
            input=validated_input,
            output=entry.metric.output,
            created_at=created_at,
            row_identity=resolution.row_identity,
        )
        await job_store.create_job_async(envelope, provenance, client=client)
        dispatcher.send_task(
            GENERIC_TASK_NAME,
            args=[envelope.model_dump(mode="json")],
            queue=entry.queue,
            task_id=job_id,
        )
        dispatched = True
    finally:
        if not dispatched:
            await _release_failed_submission(
                caller_key=request.idempotency_key,
                reservation=reservation,
                limit_consumed=limit_consumed,
                agent_scope=agent_scope,
                client=client,
            )

    return JobCreateResponse(
        job_id=job_id,
        metric=request.metric,
        status="queued",
        reused=False,
        links=job_links(job_id),
    )


__all__ = [
    "GENERIC_TASK_NAME",
    "IdempotencyConflictError",
    "SubmissionRateLimitedError",
    "SubmissionUnavailableError",
    "TaskDispatcher",
    "UnknownMetricError",
    "canonical_request_fingerprint",
    "job_links",
    "submit_job",
]
