import geopandas as gpd
from lyra.db import engine
from lyra.models import CVEGEORequest
from typing import Any
from fastapi import APIRouter, HTTPException, status
from lyra.routes.common import _validate_geodataframe, _resolve_metric
import numpy as np

router = APIRouter()


def get_gdf_from_cvegeo(
    cvegeos: list,
) -> gpd.GeoDataFrame:
    cvegeo_lengths = np.unique([len(cvegeo) for cvegeo in cvegeos])
    if len(cvegeo_lengths) > 1:
        err = f"CVEGEO values must all have the same length, but found lengths: {cvegeo_lengths}"
        raise ValueError(err)

    length_to_table_map = {2: "ent", 5: "mun", 9: "loc", 13: "ageb", 16: "mza"}
    table_name = length_to_table_map.get(cvegeo_lengths[0])
    if table_name is None:
        err = f"Unsupported CVEGEO length: {cvegeo_lengths[0]}. Supported lengths are: {list(length_to_table_map.keys())}"
        raise ValueError(err)

    with engine.connect() as conn:
        return gpd.read_postgis(
            f"""
                SELECT cvegeo, ST_Transform(geometry, 4326) AS geometry
                FROM {table_name}
                WHERE cvegeo IN %(cvegeos)s
                """,
            conn,
            params={"cvegeos": tuple(cvegeos)},
            geom_col="geometry",
        )  # ty:ignore[no-matching-overload]


@router.post("/{metric}/cvegeo")
async def metric_cvegeo(metric: str, body: CVEGEORequest) -> dict[str, Any]:
    try:
        gdf = get_gdf_from_cvegeo(body.cvegeo)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The CVEGEO list could not be processed.",
        ) from error

    _validate_geodataframe(gdf)
    calculate = _resolve_metric(metric)
    return calculate(gdf)
