from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd


def calculate_metric(zones: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return dimensionless scores from an upstream result stored in attrs."""
    return pd.DataFrame({"score": zones.attrs["upstream_scores"]})
