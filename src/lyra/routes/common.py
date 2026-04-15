from fastapi import HTTPException, status
import geopandas as gpd
from typing import Any, Callable
from lyra.processors import endpoint_map


_POLYGON_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}
_SUPPORTED_SUFFIX = ".gpkg"


def _validate_geodataframe(gdf: gpd.GeoDataFrame) -> None:
    if "cvegeo" not in gdf.columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The data must include a cvegeo column.",
        )

    if gdf.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The data does not contain any rows.",
        )

    invalid_geom_types = set(gdf.geom_type.unique()) - _POLYGON_GEOMETRY_TYPES
    if invalid_geom_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All geometries must be Polygon or MultiPolygon.",
        )


def _resolve_metric(metric: str) -> Callable[[gpd.GeoDataFrame], dict[str, Any]]:
    calculate = endpoint_map.get(metric)
    if calculate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown metric '{metric}'.",
        )

    return calculate
