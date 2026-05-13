"""Utilities for converting GeoJSON objects to GeoDataFrames."""

import geopandas as gpd
from lyra.sdk.models import GeoJSON, SingleGeoJSON


def convert_geojson_to_gdf(geojson: GeoJSON | SingleGeoJSON) -> gpd.GeoDataFrame:
    """Convert a GeoJSON or SingleGeoJSON object to a GeoDataFrame.

    The resulting GeoDataFrame uses the CRS declared in the GeoJSON object and
    is indexed by the feature IDs.

    Args:
        geojson: A ``GeoJSON`` or ``SingleGeoJSON`` object whose features will
            be converted.

    Returns:
        A GeoDataFrame with one row per feature, indexed by feature ID, and
        the CRS set from the GeoJSON's CRS property.
    """
    out = gpd.GeoDataFrame.from_features(
        [feature.model_dump(mode="json") for feature in geojson.features],
        crs=geojson.crs.properties.name,
    )
    out.index = [feature.id for feature in geojson.features]

    return out
