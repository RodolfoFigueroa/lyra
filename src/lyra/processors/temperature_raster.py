from pathlib import Path
from typing import Literal
from uuid import uuid4

import ee
import geemap

from lyra.functions.utils import convert_geojson_to_gdf, get_season_date_range
from lyra.models.wrappers import ExplicitLocationAPI


def fmask(image: ee.image.Image) -> ee.image.Image:
    """Calculates the cloud mask for a Landsat image.

    Parameters
    ----------
    image : ee.Image
        The image to analyze. Must have valid cloud bands.

    Returns
    -------
    ee.Image
        The resultant cloud mask image with binary values. A 0 indicates that a
        cloud was present.
    """

    qa = image.select("QA_PIXEL")

    dilated_cloud_bit = 1
    cloud_bit = 3
    cloud_shadow_bit = 4

    mask = qa.bitwiseAnd(1 << dilated_cloud_bit).eq(0)
    mask = mask.And(qa.bitwiseAnd(1 << cloud_bit).eq(0))
    mask = mask.And(qa.bitwiseAnd(1 << cloud_shadow_bit).eq(0))

    return image.updateMask(mask)


def reduce_landsat_collection(
    bounds: ee.geometry.Geometry,
    start_date: str,
    end_date: str,
) -> ee.image.Image:
    filtered: ee.imagecollection.ImageCollection = (
        ee.imagecollection.ImageCollection("LANDSAT/LC09/C02/T1_L2")
        .filterDate(start_date, end_date)
        .filterBounds(bounds)
    )

    if filtered.size().getInfo() == 0:
        err = "No measurements for given date and location found."
        raise ValueError(err)

    return (
        filtered.map(fmask)
        .select("ST_B10")
        .mean()
        .multiply(0.00341802)
        .add(149 - 273.15)
        .clip(bounds)
    )


METRIC_DESCRIPTION: str = (
    "Average surface temperature in degrees Celsius, derived from Landsat 9 "
    "thermal band (Band 10)."
)
RETURNS_FILE = True


def calculate(
    data: ExplicitLocationAPI,
    year: Literal[2024, 2025],
    season: Literal["spring", "summer", "autumn", "winter"],
) -> str:
    bbox = ee.Geometry.BBox(
        *convert_geojson_to_gdf(data).to_crs("EPSG:4326").total_bounds,
    )

    start_date, end_date = get_season_date_range(season, year)
    img = reduce_landsat_collection(bbox, start_date, end_date)
    fpath = Path("/lyra_cache") / f"{uuid4().hex}.tif"
    geemap.download_ee_image(
        img,
        fpath,
        region=bbox,
        crs="EPSG:4326",
        scale=30,
        resampling="near",
    )
    return str(fpath)
