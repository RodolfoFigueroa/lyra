from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
import os
import redis.asyncio as aioredis
import json

router = APIRouter()


@router.get("/download_result/{download_id}")
async def download_result(download_id: str, background_tasks: BackgroundTasks):
    redis_client = aioredis.from_url(os.environ["CELERY_BROKER_URL"])
    data_string = await redis_client.get(f"result_data_{download_id}")

    if not data_string:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    payload = json.loads(data_string)

    if payload.get("result_type") == "file":
        file_path = payload["file_path"]

        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="Result file not found")

        async def cleanup():
            try:
                os.remove(file_path)
            except OSError:
                pass
            await redis_client.delete(f"result_data_{download_id}")

        background_tasks.add_task(cleanup)
        return FileResponse(
            file_path,
            media_type="image/tiff",
            filename=os.path.basename(file_path),
        )

    return payload
