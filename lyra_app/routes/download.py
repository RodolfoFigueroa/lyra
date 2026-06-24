import contextlib

from anyio import Path
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from lyra_app import job_store
from lyra_app.db.redis import redis_client

router = APIRouter()


@router.get("/download_result/{download_id}", response_model=None)
async def download_result(
    download_id: str,
    background_tasks: BackgroundTasks,
) -> FileResponse | JSONResponse:
    pong = await redis_client.ping()
    if not pong:
        err = "Cannot connect to Redis. Please try again later."
        raise HTTPException(status_code=503, detail=err)

    payload = await job_store.get_job_result_async(download_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    if payload.get("result_type") == "file":
        file_path = Path(payload["file_path"])

        if not await file_path.exists():
            raise HTTPException(status_code=404, detail="Result file not found")

        async def cleanup() -> None:
            with contextlib.suppress(OSError):
                await file_path.unlink()
            await job_store.delete_job_result_async(download_id)

        background_tasks.add_task(cleanup)
        return FileResponse(
            file_path,
            media_type="image/tiff",
            filename=file_path.name,
        )

    return JSONResponse(content=payload)
