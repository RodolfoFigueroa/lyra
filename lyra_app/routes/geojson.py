import asyncio
import contextlib
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from lyra_app.celery_app import celery_app
from lyra_app.db.redis import redis_client
from lyra_app.registry import (
    MetricPayloadValidationError,
    get_metric_entry,
    validate_metric_payload,
)

router = APIRouter()


@router.websocket("/ws/{metric}")
async def websocket_route(websocket: WebSocket, metric: str) -> None:
    pong = await redis_client.ping()
    if not pong:
        err = "Cannot connect to Redis. Please try again later."
        raise HTTPException(status_code=503, detail=err)

    await websocket.accept()

    entry = get_metric_entry(metric)
    if entry is None:
        await websocket.send_json(
            {"status": "error", "message": f"Unknown metric: '{metric}'"},
        )
        await websocket.close(code=4404)
        return

    pubsub = redis_client.pubsub()

    try:
        raw_json = await websocket.receive_json()
        validated_data = validate_metric_payload(metric, raw_json)
        task = celery_app.send_task(
            metric,
            args=[validated_data],
            queue=entry.queue,
        )

        await websocket.send_json({"status": "queued", "task_id": task.id})

        channel_name = f"task_results_{task.id}"
        await pubsub.subscribe(channel_name)

        async def _wait_for_result() -> dict | None:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    return json.loads(message["data"])
            return None

        async def _wait_for_disconnect() -> None:
            try:
                while True:
                    await websocket.receive()
            except WebSocketDisconnect:
                return

        result_task = asyncio.create_task(_wait_for_result())
        disconnect_task = asyncio.create_task(_wait_for_disconnect())

        done, pending = await asyncio.wait(
            {result_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)

        if result_task in done:
            await websocket.send_json(result_task.result())
        else:
            celery_app.control.revoke(task.id, terminate=True, signal="SIGTERM")

    except MetricPayloadValidationError as e:
        clean_errors = e.errors
        error_message = "Input validation error: "
        for i in range(min(len(clean_errors), 5)):
            error_message += f"\n - {clean_errors[i]}"
        if len(clean_errors) > 5:
            error_message += f"\n - ... and {len(clean_errors) - 5} more errors."

        await websocket.send_json(
            {"status": "error", "error_type": "validation", "message": error_message},
        )
        await websocket.close(code=4404)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()

        with contextlib.suppress(RuntimeError):
            await websocket.close()
