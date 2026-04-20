import os
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from lyra.worker import celery_app
import redis.asyncio as aioredis
import geopandas as gpd
from fastapi import HTTPException, status
from lyra.models import (
    GeoJSONRequest,
    AccessibilityGeoJSONRequest,
    JobAccessibilityGeoJSONRequest,
    GeoJSON,
)
from lyra.processors import endpoint_map
from lyra.functions.utils import convert_geojson_to_gdf
from lyra.routes.common import _validate_geodataframe, _resolve_metric
from typing import Any

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


@router.post("/accessibility_services/geojson")
async def metric_accessibility_services_geojson(
    body: AccessibilityGeoJSONRequest,
) -> dict[str, Any]:
    gdf = _convert_geojson_and_validate(body.geojson)
    gdf_public_spaces = _convert_geojson_and_validate(body.geojson_public)
    return endpoint_map["accessibility_services"](gdf, gdf_public_spaces)


@router.post("/accessibility_jobs/geojson")
async def metric_accessibility_jobs_geojson(
    body: JobAccessibilityGeoJSONRequest,
) -> dict[str, Any]:
    gdf = _convert_geojson_and_validate(body.geojson)
    return endpoint_map["accessibility_jobs"](gdf, body.group_patterns)


@router.post("/{metric}/geojson")
async def metric_geojson(metric: str, body: GeoJSONRequest) -> dict[str, Any]:
    gdf = _convert_geojson_and_validate(body.geojson)
    calculate = _resolve_metric(metric)
    return calculate(gdf)


@router.websocket("/ws/{metric}/geojson")
async def websocket_metric_geojson(websocket: WebSocket, metric: str):
    await websocket.accept()

    redis_client = aioredis.from_url(os.environ["CELERY_BROKER_URL"])
    pubsub = redis_client.pubsub()

    try:
        data = GeoJSONRequest(**await websocket.receive_json())

        if metric not in celery_app.tasks:
            await websocket.send_json(
                {
                    "status": "error",
                    "message": f"Task '{metric}' is not registered.",
                }
            )
            return

        task = celery_app.send_task(metric, args=[data.geojson.model_dump(mode="json")])

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
