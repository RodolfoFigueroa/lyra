import geopandas as gpd
from fastapi import APIRouter, HTTPException, status
from lyra.models import (
    GeoJSONRequest,
    AccessibilityGeoJSONRequest,
    JobAccessibilityGeoJSONRequest,
)
from lyra.processors import endpoint_map
from lyra.routes.common import _validate_geodataframe, _resolve_metric
from typing import Any

router = APIRouter()


# TODO: Replace with middleware that extracts agebs and passes them to the metric function


@router.post("/accessibility_services/geojson")
async def metric_accessibility_services_geojson(
    body: AccessibilityGeoJSONRequest,
) -> dict[str, Any]:
    try:
        gdf = gpd.GeoDataFrame.from_features(
            body.geojson.features,
            crs=body.geojson.crs.properties.name,
        )
        gdf_public_spaces = gpd.GeoDataFrame.from_features(
            body.geojson_public.features,
            crs=body.geojson_public.crs.properties.name,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The request body could not be parsed as a GeoDataFrame.",
        ) from error

    _validate_geodataframe(gdf)
    return endpoint_map["accessibility_services"](gdf, gdf_public_spaces)


@router.post("/accessibility_jobs/geojson")
async def metric_accessibility_jobs_geojson(
    body: JobAccessibilityGeoJSONRequest,
) -> dict[str, Any]:
    try:
        gdf = gpd.GeoDataFrame.from_features(
            body.geojson.features,
            crs=body.geojson.crs.properties.name,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The request body could not be parsed as a GeoDataFrame.",
        ) from error

    _validate_geodataframe(gdf)
    return endpoint_map["accessibility_jobs"](gdf, body.group_patterns)


@router.post("/{metric}/geojson")
async def metric_geojson(metric: str, body: GeoJSONRequest) -> dict[str, Any]:
    try:
        gdf = gpd.GeoDataFrame.from_features(
            body.geojson.features,
            crs=body.geojson.crs.properties.name,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The request body could not be parsed as a GeoDataFrame.",
        ) from error

    _validate_geodataframe(gdf)
    calculate = _resolve_metric(metric)
    return calculate(gdf)
