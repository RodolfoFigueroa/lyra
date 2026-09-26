"""Controlled Earth Engine workflow for offline adaptation trials.

Compute mean elevation in metres from USGS/SRTMGL1_003's elevation band, reducing
at 30 metres. Preserve all zone IDs and their order; missing source pixels produce
null (NaN) mean_elevation cells, never zero. No ordinary parameters or feature
attributes are required. Input polygons have a declared CRS; the calculation
reprojects them to EPSG:4326. Geographic applicability limits are undocumented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ee
import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd


def calculate_metric(zones: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return one nullable floating-point mean_elevation per zone."""
    geographic = zones.to_crs("EPSG:4326")
    source = ee.Image("USGS/SRTMGL1_003").select("elevation")
    values = [
        source.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=ee.Geometry(geometry.__geo_interface__),
            scale=30,
        )
        .get("elevation")
        .getInfo()
        for geometry in geographic.geometry
    ]
    return pd.DataFrame({"mean_elevation": values}, index=zones.index, dtype=float)
