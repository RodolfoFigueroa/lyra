import calendar
from typing import Literal

import geopandas as gpd
import pandana as pdna
import pandas as pd


def get_geometries_osmid(
    geometries: gpd.GeoDataFrame,
    net_accessibility: pdna.Network,
    *,
    mapping_distance: float = 1000,
) -> pd.Series:
    return net_accessibility.get_node_ids(
        x_col=geometries["geometry"].centroid.x,
        y_col=geometries["geometry"].centroid.y,
        # Despite what pandana documentation says, this mapping distance is
        # just standard Euclidean, not based on network impedance. Thus, we
        # don't need to scale it.
        mapping_distance=mapping_distance,
    )


def get_date_range(month: int, year: int) -> tuple[str, str]:
    month_str = str(month).rjust(2, "0")

    start = f"{year}-{month_str}-01"

    _, end_day = calendar.monthrange(year, month)
    end_day_str = str(end_day).rjust(2, "0")
    end = f"{year}-{month_str}-{end_day_str}"

    return start, end


def get_season_date_range(
    season: Literal["winter", "spring", "summer", "autumn"],
    year: int,
) -> tuple[str, str]:
    if season == "winter":
        start, _ = get_date_range(12, year - 1)
        _, end = get_date_range(2, year)
    elif season == "spring":
        start, _ = get_date_range(3, year)
        _, end = get_date_range(5, year)
    elif season == "summer":
        start, _ = get_date_range(6, year)
        _, end = get_date_range(8, year)
    elif season == "autumn":
        start, _ = get_date_range(9, year)
        _, end = get_date_range(11, year)
    else:
        err = (
            f"Invalid season: {season}. Must be one of 'winter', 'spring', "
            "'summer', or 'autumn'."
        )
        raise ValueError(err)

    return start, end
