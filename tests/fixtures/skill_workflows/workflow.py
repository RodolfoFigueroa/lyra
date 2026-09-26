"""A deterministic, offline workflow for skill adaptation trials.

Input zones are EPSG:4326 Polygon or MultiPolygon features, indexed by unique
string feature IDs. Every zone has a finite numeric base_value and a string
category. This illustrative score is valid for either geometry type at any zone
size because it uses supplied properties, not geometric measurements.
Parameters have no defaults or domain restrictions beyond their annotated types;
parameter_2 and the calculated scores must be finite. Output is non-nullable,
measured in score units, and preserves every input identifier and its order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd


def calculate_metric(
    zones: gpd.GeoDataFrame,
    parameter_1: int,
    parameter_2: float,
    parameter_3: list[str],
) -> pd.DataFrame:
    """Multiply selected base values and add an offset; unselected scores are zero."""
    scores = (zones["base_value"] * parameter_1 + parameter_2).where(
        zones["category"].isin(parameter_3), 0.0
    )
    return pd.DataFrame({"score": scores.astype(float)}, index=zones.index)
