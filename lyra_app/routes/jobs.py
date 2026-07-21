"""HTTP endpoints for submitting and inspecting metric jobs."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, cast
from uuid import uuid4

from anyio import Path
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from lyra.sdk.models import (
    FileJobResult,
    JobCreateRequest,
    JobCreateResponse,
    JobLifecycleEvent,
    JobStatusInfo,
    TableJobResult,
    build_table_preview,
    parse_job_result,
    result_ref_for_job,
)
from redis.exceptions import RedisError

from lyra_app import job_store
from lyra_app.agent_auth import require_agent_key
from lyra_app.celery_app import celery_app
from lyra_app.config import get_config
from lyra_app.db.connection import DatabaseUnavailableError
from lyra_app.db.dependencies import get_database_runtime
from lyra_app.db.redis import redis_client
from lyra_app.job_submission import (
    IdempotencyConflictError,
    SubmissionRateLimitedError,
    SubmissionUnavailableError,
    UnknownMetricError,
    submit_job,
)
from lyra_app.registry import MetricPayloadValidationError
from lyra_app.routes.errors import database_unavailable_http_exception
from lyra_app.spatial_inputs import (
    SpatialInputResolutionUnavailableError,
    SpatialInputValidationError,
)
from lyra_app.worker_control import reconcile_celery_failure

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from lyra_app.db.connection import ApplicationDatabaseRuntime
    from lyra_app.job_submission import SubmissionRedisClient

DatabaseRuntimeDependency = Annotated[
    "ApplicationDatabaseRuntime | None", Depends(get_database_runtime)
]

router = APIRouter(tags=["Jobs"], dependencies=[Depends(require_agent_key)])

TERMINAL_EVENTS = {"succeeded", "failed", "cancelled"}
SSE_KEEPALIVE = ": keepalive\n\n"


async def _ensure_redis_available() -> None:
    try:
        pong = await redis_client.ping()
    except RedisError as exc:
        err = "Cannot connect to Redis. Please try again later."
        raise HTTPException(status_code=503, detail=err) from exc
    if not pong:
        err = "Cannot connect to Redis. Please try again later."
        raise HTTPException(status_code=503, detail=err)


async def _get_reconciled_job_status(
    job_id: str,
) -> job_store.JobStatusSnapshot | None:
    snapshot = await job_store.get_job_status_async(job_id)
    if snapshot is None:
        return None
    return await reconcile_celery_failure(snapshot)


def _sse_message(stored_event: job_store.StoredJobEvent) -> str:
    payload = stored_event.event.model_dump(mode="json")
    data = json.dumps(payload, separators=(",", ":"))
    return (
        f"id: {stored_event.stream_id}\n"
        f"event: {stored_event.event.kind}\n"
        f"data: {data}\n\n"
    )


def _result_status_payload(
    snapshot: job_store.JobStatusSnapshot,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": snapshot.job_id,
        "status": snapshot.status,
        "updated_at": snapshot.updated_at.isoformat(),
        "result_ref": result_ref_for_job(snapshot.job_id),
        "detail": "Result is not available yet",
    }
    if snapshot.metric is not None:
        payload["metric"] = snapshot.metric
    if snapshot.error is not None:
        payload["error"] = snapshot.error
        payload["detail"] = "Job finished without a successful result"
    if snapshot.progress is not None:
        payload["progress"] = snapshot.progress.model_dump(
            mode="json",
            exclude_none=True,
        )
    if snapshot.latest_message is not None:
        payload["latest_message"] = snapshot.latest_message.model_dump(
            mode="json", exclude_none=True
        )
    return payload


def _is_terminal_event(event: object) -> bool:
    return isinstance(event, JobLifecycleEvent) and event.status in TERMINAL_EVENTS


def _stream_id_parts(value: str) -> tuple[int, int]:
    try:
        milliseconds, sequence = value.split("-", maxsplit=1)
        return int(milliseconds), int(sequence)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Last-Event-ID") from exc


def _table_jsonl_stream(result: TableJobResult) -> Iterator[str]:
    index_field = build_table_preview(result, row_limit=0).index_field
    for result_index, values in zip(result.index, result.data, strict=True):
        row = {index_field: result_index}
        row.update(dict(zip(result.columns, values, strict=True)))
        yield json.dumps(row, separators=(",", ":")) + "\n"


async def _job_event_stream(
    job_id: str,
    request: Request,
    *,
    last_event_id: str | None = None,
) -> AsyncIterator[str]:
    next_event_id = last_event_id
    while True:
        events = await job_store.read_job_events_async(
            job_id,
            after_id=next_event_id,
        )
        for event in events:
            next_event_id = event.stream_id
            yield _sse_message(event)
            if _is_terminal_event(event.event):
                return

        if not events:
            snapshot = await _get_reconciled_job_status(job_id)
            if snapshot is None or snapshot.status in TERMINAL_EVENTS:
                return

        if await request.is_disconnected():
            return

        events = await job_store.read_new_job_events_async(
            job_id,
            after_id=next_event_id or job_store.STREAM_LATEST,
        )
        if not events:
            yield SSE_KEEPALIVE
            continue

        for event in events:
            next_event_id = event.stream_id
            yield _sse_message(event)
            if _is_terminal_event(event.event):
                return


async def create_job(
    request: JobCreateRequest,
    database: ApplicationDatabaseRuntime | None = None,
) -> JobCreateResponse:
    """Submit a validated job request and translate domain failures to HTTP errors.

    Returns:
        Queued or idempotently reused job metadata.

    Raises:
        HTTPException: If the request, metric, capacity, or backing service prevents
            submission.
    """
    try:
        return await submit_job(
            request,
            client=cast("SubmissionRedisClient", redis_client),
            dispatcher=celery_app,
            job_id_factory=lambda: uuid4().hex,
            database=database,
        )
    except UnknownMetricError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except MetricPayloadValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except SpatialInputValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except (DatabaseUnavailableError, SpatialInputResolutionUnavailableError) as exc:
        runtime_config = database.config if database is not None else get_config()
        error = database_unavailable_http_exception(runtime_config)
        raise error from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_conflict",
                "message": str(exc),
                **exc.details,
            },
        ) from exc
    except SubmissionRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": str(exc),
                **exc.details,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except SubmissionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_job_route(
    request: JobCreateRequest,
    database: DatabaseRuntimeDependency,
) -> JobCreateResponse:
    """Handle authenticated HTTP job submission.

    Returns:
        Queued or idempotently reused job metadata.
    """
    return await create_job(request, database)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JobStatusInfo:
    """Return the latest reconciled status for a retained job.

    Returns:
        The current lifecycle, progress, message, and error metadata.

    Raises:
        HTTPException: If Redis is unavailable or the job is no longer retained.
    """
    await _ensure_redis_available()
    snapshot = await _get_reconciled_job_status(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job expired or not found")
    return JobStatusInfo.model_validate(snapshot.model_dump(mode="json"))


@router.get("/jobs/{job_id}/events", response_model=None)
async def get_job_events(
    job_id: str,
    request: Request,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID"),
    ] = None,
) -> StreamingResponse:
    """Stream retained and live job events using Server-Sent Events.

    Returns:
        An event stream beginning after the optional cursor.

    Raises:
        HTTPException: If the job is absent, Redis is unavailable, or the requested
            cursor predates retained history.
    """
    await _ensure_redis_available()
    snapshot = await _get_reconciled_job_status(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job expired or not found")
    if last_event_id is not None and last_event_id != job_store.STREAM_START:
        earliest = await job_store.read_job_events_async(job_id, count=1)
        if earliest and _stream_id_parts(last_event_id) < _stream_id_parts(
            earliest[0].stream_id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "event_cursor_gap",
                    "message": "Requested job event history is no longer retained.",
                    "earliest_event_id": earliest[0].stream_id,
                },
            )

    return StreamingResponse(
        _job_event_stream(job_id, request, last_event_id=last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/jobs/{job_id}/result", response_model=None)
async def get_job_result(job_id: str) -> JSONResponse:
    """Return the raw terminal result retained for a job.

    Returns:
        The validated terminal result as a JSON response.

    Raises:
        HTTPException: If Redis is unavailable or the result is not retained.
    """
    await _ensure_redis_available()
    payload = await job_store.get_job_result_async(job_id)
    if payload is None:
        await _get_reconciled_job_status(job_id)
        payload = await job_store.get_job_result_async(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    result = parse_job_result(payload)
    return JSONResponse(content=result.model_dump(mode="json", exclude_none=True))


@router.get("/jobs/{job_id}/result/descriptor", response_model=None)
async def get_job_result_descriptor(job_id: str) -> JSONResponse:
    """Return result metadata or current non-success status for a job.

    Returns:
        A terminal descriptor, failure metadata, or an accepted running response.

    Raises:
        HTTPException: If Redis is unavailable or a successful result has expired.
    """
    await _ensure_redis_available()
    descriptor = await job_store.get_job_result_descriptor_async(job_id)
    if descriptor is not None:
        return JSONResponse(
            content=descriptor.model_dump(mode="json", exclude_none=True),
        )

    snapshot = await _get_reconciled_job_status(job_id)
    if snapshot is not None and job_store.is_terminal_status(snapshot.status):
        descriptor = await job_store.get_job_result_descriptor_async(job_id)
        if descriptor is not None:
            return JSONResponse(
                content=descriptor.model_dump(mode="json", exclude_none=True),
            )
    if snapshot is None or snapshot.status == "succeeded":
        raise HTTPException(status_code=404, detail="Result expired or not found")

    status_code = (
        status.HTTP_200_OK
        if job_store.is_terminal_status(snapshot.status)
        else status.HTTP_202_ACCEPTED
    )
    return JSONResponse(
        content=_result_status_payload(snapshot),
        status_code=status_code,
    )


@router.get("/jobs/{job_id}/result/table.jsonl", response_model=None)
async def export_job_result_jsonl(job_id: str) -> StreamingResponse:
    """Stream a retained table result as newline-delimited JSON.

    Returns:
        A downloadable JSONL response with one object per table row.

    Raises:
        HTTPException: If the result is absent or is not a table.
    """
    await _ensure_redis_available()
    payload = await job_store.get_job_result_async(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    result = parse_job_result(payload)
    if not isinstance(result, TableJobResult):
        raise HTTPException(status_code=409, detail="Job result is not a table")

    return StreamingResponse(
        _table_jsonl_stream(result),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}.jsonl"',
        },
    )


@router.get("/jobs/{job_id}/result/download", response_model=None)
async def download_job_result(job_id: str) -> FileResponse:
    """Download the retained artifact for a file-producing job.

    Returns:
        A file response using the result's media type and filename.

    Raises:
        HTTPException: If the result or artifact is absent or is not a file result.
    """
    await _ensure_redis_available()
    payload = await job_store.get_job_result_async(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    result = parse_job_result(payload)
    if isinstance(result, FileJobResult):
        file_path = Path(result.file_path)
        if not await file_path.exists():
            raise HTTPException(status_code=404, detail="Result file not found")

        return FileResponse(
            file_path,
            media_type=result.media_type,
            filename=file_path.name,
        )
    raise HTTPException(status_code=409, detail="Job result is not a file")
