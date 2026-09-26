from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd


def calculate_metric(zones: gpd.GeoDataFrame) -> pd.DataFrame:
    return pd.DataFrame({"risk": zones["value"] * 2}, index=zones.index)
