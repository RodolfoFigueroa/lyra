from pydantic import ValidationError
import os
from lyra.registry import TASK_REGISTRY
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from lyra.worker import celery_app
import redis.asyncio as aioredis

router = APIRouter()


@router.websocket("/ws/{metric}/geojson")
async def websocket_route(websocket: WebSocket, metric: str):
    await websocket.accept()

    if metric not in TASK_REGISTRY:
        await websocket.send_json(
            {"status": "error", "message": f"Unknown metric: '{metric}'"}
        )
        await websocket.close(code=4404)
        return

    redis_client = aioredis.from_url(os.environ["CELERY_BROKER_URL"])
    pubsub = redis_client.pubsub()

    try:
        RequestModel = TASK_REGISTRY[metric]["model"]

        raw_json = await websocket.receive_json()

        # Raises ValidationError if the input doesn't match the expected schema for this metric
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
        await websocket.send_json(
            {"status": "error", "type": "validation_error", "details": e.errors()}
        )
        await websocket.close(code=4404)
    except WebSocketDisconnect:
        print("Client disconnected before the job finished.")
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()

        try:
            await websocket.close()
        except RuntimeError:
            pass
