from pydantic import ValidationError
import os
from lyra.registry import TASK_REGISTRY
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from lyra.worker import celery_app
import redis.asyncio as aioredis
import geopandas as gpd
from fastapi import HTTPException, status
from lyra.models import (
    GeoJSON,
)
from lyra.functions.utils import convert_geojson_to_gdf
from lyra.routes.common import _validate_geodataframe

router = APIRouter()


# TODO: Replace with middleware that extracts agebs and passes them to the metric function


def _convert_geojson_and_validate(geojson: GeoJSON) -> gpd.GeoDataFrame:
    try:
        gdf = convert_geojson_to_gdf(geojson)
        _validate_geodataframe(gdf)
        return gdf
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The request body could not be parsed as a GeoDataFrame.",
        ) from error


# @router.post("/accessibility_services/geojson")
# async def metric_accessibility_services_geojson(
#     body: ServiceAccessibilityGeoJSONRequest,
# ) -> dict[str, Any]:
#     gdf = _convert_geojson_and_validate(body.geojson)
#     gdf_public_spaces = _convert_geojson_and_validate(body.geojson_public)
#     return endpoint_map["accessibility_services"](gdf, gdf_public_spaces)


# @router.post("/accessibility_jobs/geojson")
# async def metric_accessibility_jobs_geojson(
#     body: JobAccessibilityGeoJSONRequest,
# ) -> dict[str, Any]:
#     gdf = _convert_geojson_and_validate(body.geojson)
#     return endpoint_map["accessibility_jobs"](gdf, body.group_patterns)


# @router.post("/{metric}/geojson")
# async def metric_geojson(metric: str, body: GeoJSONRequest) -> dict[str, Any]:
#     gdf = _convert_geojson_and_validate(body.geojson)
#     calculate = _resolve_metric(metric)
#     return calculate(gdf)


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
    except WebSocketDisconnect:
        print("Client disconnected before the job finished.")
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()

        try:
            await websocket.close()
        except RuntimeError:
            pass
