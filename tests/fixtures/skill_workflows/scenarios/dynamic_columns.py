from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd


def calculate_metric(zones: gpd.GeoDataFrame, categories: list[str]) -> pd.DataFrame:
    """Return one count column per requested category, preserving zone IDs/order."""
    return pd.DataFrame(
        {name: (zones["category"] == name).astype(int) for name in categories},
        index=zones.index,
    )
