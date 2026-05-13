import asyncio
import contextlib
import json
import os

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from lyra_app.registry import TASK_REGISTRY
from lyra_app.worker import celery_app

router = APIRouter()


@router.websocket("/ws/{metric}")
async def websocket_route(websocket: WebSocket, metric: str) -> None:
    await websocket.accept()

    if metric not in TASK_REGISTRY:
        await websocket.send_json(
            {"status": "error", "message": f"Unknown metric: '{metric}'"},
        )
        await websocket.close(code=4404)
        return

    redis_client = aioredis.from_url(os.environ["CELERY_BROKER_URL"])
    pubsub = redis_client.pubsub()

    try:
        RequestModel = TASK_REGISTRY[metric]["model"]  # noqa: N806

        raw_json = await websocket.receive_json()

        # Raises ValidationError if the input doesn't match the expected schema
        # for this metric
        validated_data = RequestModel(**raw_json)

        task = celery_app.send_task(metric, args=[validated_data.model_dump()])

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

    except ValidationError as e:
        clean_errors = [
            {"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")}
            for err in e.errors()
        ]

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
