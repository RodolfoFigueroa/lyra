import contextlib
import json
import os

import redis.asyncio as aioredis
from anyio import Path
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/download_result/{download_id}", response_model=None)
async def download_result(
    download_id: str, background_tasks: BackgroundTasks
) -> FileResponse | dict:
    redis_client = aioredis.from_url(os.environ["CELERY_BROKER_URL"])
    data_string = await redis_client.get(f"result_data_{download_id}")

    if not data_string:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    payload = json.loads(data_string)

    if payload.get("result_type") == "file":
        file_path = Path(payload["file_path"])

        if not await file_path.exists():
            raise HTTPException(status_code=404, detail="Result file not found")

        async def cleanup() -> None:
            with contextlib.suppress(OSError):
                await file_path.unlink()
            await redis_client.delete(f"result_data_{download_id}")

        background_tasks.add_task(cleanup)
        return FileResponse(
            file_path,
            media_type="image/tiff",
            filename=file_path.name,
        )

    return payload
