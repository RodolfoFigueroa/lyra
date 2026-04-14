from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable

import geopandas as gpd
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from lyra.functions import endpoint_map, get_gdf_from_cvegeo


router = APIRouter()

_SUPPORTED_SUFFIX = ".gpkg"
_POLYGON_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}


class CVEGEORequest(BaseModel):
    cvegeo: list[str] = Field(..., min_length=1)
    table_name: str = Field(..., min_length=1)


@router.post("/{metric}/file")
async def metric_file(metric: str, file: UploadFile = File(...)) -> dict[str, Any]:
    filename = file.filename or ""
    if Path(filename).suffix.lower() != _SUPPORTED_SUFFIX:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .gpkg uploads are supported.",
        )

    try:
        gdf = await _load_geodataframe_from_upload(file)
        calculate = _resolve_metric(metric)
        return calculate(gdf)
    except HTTPException:
        raise
    finally:
        await file.close()


@router.post("/{metric}/geojson")
async def metric_geojson(metric: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        gdf = gpd.GeoDataFrame.from_features(
            body.get("features", []),
            crs=body.get("crs", {}).get("properties", {}).get("name") or "EPSG:4326",
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


@router.post("/{metric}/cvegeo")
async def metric_cvegeo(metric: str, body: CVEGEORequest) -> dict[str, Any]:
    try:
        gdf = get_gdf_from_cvegeo(body.cvegeo, body.table_name)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The CVEGEO list could not be processed.",
        ) from error

    _validate_geodataframe(gdf)
    calculate = _resolve_metric(metric)
    return calculate(gdf)


async def _load_geodataframe_from_upload(file: UploadFile) -> gpd.GeoDataFrame:
    suffix = Path(file.filename or "upload.gpkg").suffix or _SUPPORTED_SUFFIX

    with NamedTemporaryFile(suffix=suffix) as temporary_file:
        while chunk := await file.read(1024 * 1024):
            temporary_file.write(chunk)
        temporary_file.flush()
        gdf = _read_geodataframe_from_file(temporary_file.name)

    _validate_geodataframe(gdf)
    return gdf


def _read_geodataframe_from_file(file_path: str) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(file_path)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file could not be read as a geopackage.",
        ) from error


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
