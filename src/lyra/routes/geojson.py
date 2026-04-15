import geopandas as gpd
from fastapi import APIRouter, HTTPException, status
from lyra.routes.models import GeoJSONRequest
from lyra.routes.common import _validate_geodataframe, _resolve_metric
from typing import Any

router = APIRouter()


@router.post("/{metric}/geojson")
async def metric_geojson(metric: str, body: GeoJSONRequest) -> dict[str, Any]:
    try:
        gdf = gpd.GeoDataFrame.from_features(
            body.geojson.features,
            crs=body.geojson.crs.properties.name,
        )
        # GeoDataFrame.from_features does not restore non-geometry properties
        # that geopandas' to_json embeds inside each feature; they are already
        # in feature["properties"], so from_features handles them automatically.
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The request body could not be parsed as a GeoDataFrame.",
        ) from error

    _validate_geodataframe(gdf)
    calculate = _resolve_metric(metric)
    return calculate(gdf)
