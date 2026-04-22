from fastapi import APIRouter, HTTPException
import os
import redis.asyncio as aioredis
import json

router = APIRouter()


@router.get("/download-result/{download_id}")
async def download_result(download_id: str):
    redis_client = aioredis.from_url(os.environ["CELERY_BROKER_URL"])
    data_string = await redis_client.get(f"result_data_{download_id}")
    print(data_string)

    if not data_string:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    return json.loads(data_string)
