import functools
from typing import Literal

import ee

from lyra.functions.temperature import reduce_landsat_collection
from lyra.functions.utils import (
    get_season_date_range,
    reduce_ee_image_over_gdf_factory,
)
from lyra.models.processors.temperature import AllowedLandsatYears
from lyra.models.wrappers import ExplicitLocationAPI

METRIC_DESCRIPTION = (
    "Average surface temperature in degrees Celsius, derived from Landsat 9 "
    "thermal band (Band 10)."
)


def calculate(
    data: ExplicitLocationAPI,
    year: AllowedLandsatYears,
    season: Literal["spring", "summer", "autumn", "winter"],
) -> dict:
    start_date, end_date = get_season_date_range(season, year)
    load_img_func = functools.partial(
        reduce_landsat_collection,
        start_date=start_date,
        end_date=end_date,
    )
    return reduce_ee_image_over_gdf_factory(
        load_img_func, reducer=ee.Reducer.mean(), scale=30
    )(data)
