from lyra.db import get_gdf_from_cvegeo
from lyra.models import CVEGEORequest
from typing import Any
from fastapi import APIRouter, HTTPException, status
from lyra.routes.common import _validate_geodataframe, _resolve_metric

router = APIRouter()


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
