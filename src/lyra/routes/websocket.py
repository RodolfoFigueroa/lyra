import os
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from lyra.worker import celery_app
import redis.asyncio as aioredis

router = APIRouter()


class DynamicRegionRequest(BaseModel):
    function_name: str
    geojson: dict


@router.websocket("/ws/analyze")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    redis_client = aioredis.from_url(os.environ["CELERY_BROKER_URL"])
    pubsub = redis_client.pubsub()

    try:
        data = await websocket.receive_json()

        function_name = data["function_name"]
        geojson = data["geojson"]

        if function_name not in celery_app.tasks:
            await websocket.send_json(
                {
                    "status": "error",
                    "message": f"Task '{function_name}' is not registered.",
                }
            )
            return

        task = celery_app.send_task(function_name, args=[geojson])

        await websocket.send_json({"status": "queued", "task_id": task.id})

        channel_name = f"task_results_{task.id}"
        await pubsub.subscribe(channel_name)

        async for message in pubsub.listen():
            if message["type"] == "message":
                notification = json.loads(message["data"])
                await websocket.send_json(notification)
                break
    except WebSocketDisconnect:
        print("Client disconnected before job finished.")
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()

        try:
            await websocket.close()
        except RuntimeError:
            pass
