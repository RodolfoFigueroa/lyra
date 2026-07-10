import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

from anyio import Path
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from lyra.sdk.models import (
    FileJobResult,
    JobCreateRequest,
    JobCreateResponse,
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
from lyra_app.db.redis import redis_client
from lyra_app.job_submission import (
    IdempotencyConflictError,
    SubmissionRateLimitedError,
    SubmissionUnavailableError,
    UnknownMetricError,
    submit_job,
)
from lyra_app.registry import MetricPayloadValidationError
from lyra_app.spatial_inputs import (
    SpatialInputResolutionUnavailableError,
    SpatialInputValidationError,
)

router = APIRouter(dependencies=[Depends(require_agent_key)])

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


def _sse_message(stored_event: job_store.StoredJobEvent) -> str:
    payload = stored_event.event.model_dump(mode="json")
    data = json.dumps(payload, separators=(",", ":"))
    return (
        f"id: {stored_event.stream_id}\n"
        f"event: {stored_event.event.event}\n"
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
    return payload


async def _table_jsonl_stream(result: TableJobResult) -> AsyncIterator[str]:
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
            if event.event.event in TERMINAL_EVENTS:
                return

        if not events:
            snapshot = await job_store.get_job_status_async(job_id)
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
            if event.event.event in TERMINAL_EVENTS:
                return


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_job(request: JobCreateRequest) -> JobCreateResponse:
    try:
        return await submit_job(
            request,
            client=redis_client,
            dispatcher=celery_app,
            job_id_factory=lambda: uuid4().hex,
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
    except SpatialInputResolutionUnavailableError as exc:
        err = "Cannot resolve spatial input. Please try again later."
        raise HTTPException(status_code=503, detail=err) from exc
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


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JobStatusInfo:
    await _ensure_redis_available()
    snapshot = await job_store.get_job_status_async(job_id)
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
    await _ensure_redis_available()
    snapshot = await job_store.get_job_status_async(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job expired or not found")

    return StreamingResponse(
        _job_event_stream(job_id, request, last_event_id=last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/jobs/{job_id}/result", response_model=None)
async def get_job_result(job_id: str) -> JSONResponse:
    await _ensure_redis_available()
    payload = await job_store.get_job_result_async(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    result = parse_job_result(payload)
    return JSONResponse(content=result.model_dump(mode="json", exclude_none=True))


@router.get("/jobs/{job_id}/result/descriptor", response_model=None)
async def get_job_result_descriptor(job_id: str) -> JSONResponse:
    await _ensure_redis_available()
    descriptor = await job_store.get_job_result_descriptor_async(job_id)
    if descriptor is not None:
        return JSONResponse(
            content=descriptor.model_dump(mode="json", exclude_none=True),
        )

    snapshot = await job_store.get_job_status_async(job_id)
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
