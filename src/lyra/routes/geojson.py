import contextlib
import json
import os

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from lyra.registry import TASK_REGISTRY
from lyra.worker import celery_app

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

        async for message in pubsub.listen():
            if message["type"] == "message":
                notification = json.loads(message["data"])

                await websocket.send_json(notification)
                break

    except ValidationError as e:
        clean_errors = [
            {"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")}
            for err in e.errors()
        ]
        await websocket.send_json(
            {"status": "error", "type": "validation_error", "details": clean_errors},
        )
        await websocket.close(code=4404)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()

        with contextlib.suppress(RuntimeError):
            await websocket.close()
