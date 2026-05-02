from pathlib import Path
from typing import Literal
from uuid import uuid4

import ee
import geemap

from lyra.functions.temperature import reduce_landsat_collection
from lyra.functions.utils import convert_geojson_to_gdf, get_season_date_range, convert_polygon_to_ee
from lyra.models.processors.temperature import AllowedLandsatYears
from lyra.models.wrappers import ExplicitBoundsAPI

METRIC_DESCRIPTION: str = (
    "Surface temperature raster in degrees Celsius, derived from Landsat 9 "
    "thermal band (Band 10)."
)
RETURNS_FILE = True


def calculate(
    data: ExplicitBoundsAPI,
    year: AllowedLandsatYears,
    season: Literal["spring", "summer", "autumn", "winter"],
) -> str:
    gdf = convert_geojson_to_gdf(data).to_crs("EPSG:4326")
    bounds = convert_polygon_to_ee(gdf["geometry"].iloc[0])

    start_date, end_date = get_season_date_range(season, year)
    img = reduce_landsat_collection(bounds, start_date, end_date)
    fpath = Path("/lyra_cache") / f"{uuid4().hex}.tif"
    geemap.download_ee_image(
        img,
        fpath,
        region=bounds,
        crs="EPSG:4326",
        scale=30,
        resampling="near",
    )
    return str(fpath)
