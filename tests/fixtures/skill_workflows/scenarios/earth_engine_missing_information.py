"""Controlled Earth Engine workflow with an undocumented source meaning.

Preserve all zone IDs and their order; missing source pixels produce null (NaN)
mean_signal cells, never zero. No ordinary parameters or feature attributes are
required. Input polygons have a declared CRS; the calculation reprojects them to
EPSG:4326 and reduces at 30 metres. Source-band meaning, output units, and
geographic applicability limits are undocumented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ee
import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd


def calculate_metric(zones: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return one nullable floating-point mean_signal per zone."""
    geographic = zones.to_crs("EPSG:4326")
    source = ee.Image("projects/example/assets/signal").select("value")
    values = [
        source.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=ee.Geometry(geometry.__geo_interface__),
            scale=30,
        )
        .get("value")
        .getInfo()
        for geometry in geographic.geometry
    ]
    return pd.DataFrame({"mean_signal": values}, index=zones.index, dtype=float)
