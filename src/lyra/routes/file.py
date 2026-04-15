from fastapi import APIRouter, UploadFile, HTTPException, status, File
from typing import Any
from pathlib import Path
from lyra.routes.common import (
    _validate_geodataframe,
    _resolve_metric,
    _SUPPORTED_SUFFIX,
)
from tempfile import NamedTemporaryFile
import geopandas as gpd

router = APIRouter()


def _read_geodataframe_from_file(file_path: str) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(file_path)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file could not be read as a geopackage.",
        ) from error


async def _load_geodataframe_from_upload(file: UploadFile) -> gpd.GeoDataFrame:
    suffix = Path(file.filename or "upload.gpkg").suffix or _SUPPORTED_SUFFIX

    with NamedTemporaryFile(suffix=suffix) as temporary_file:
        while chunk := await file.read(1024 * 1024):
            temporary_file.write(chunk)
        temporary_file.flush()
        gdf = _read_geodataframe_from_file(temporary_file.name)

    _validate_geodataframe(gdf)
    return gdf


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
