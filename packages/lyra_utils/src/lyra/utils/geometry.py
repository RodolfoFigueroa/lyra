"""Utilities for converting GeoJSON objects to GeoDataFrames."""

import math

import geopandas as gpd
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON

AREA_CRS = "EPSG:6372"


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


def calculate_feature_areas_m2(geojson: GeoJSON) -> dict[str, float]:
    """Calculate valid polygon feature areas in Lyra's canonical Mexico CRS."""

    feature_ids = [str(feature.id) for feature in geojson.features]
    if len(feature_ids) != len(set(feature_ids)):
        msg = "Location feature IDs must be unique to calculate areas."
        raise ValueError(msg)

    try:
        gdf = convert_geojson_to_gdf(geojson)
    except (RuntimeError, ValueError) as exc:
        source_crs = geojson.crs.properties.name
        msg = f"Location geometry could not be interpreted in {source_crs}."
        raise ValueError(msg) from exc
    geometry_types = set(gdf.geometry.geom_type)
    unsupported_types = sorted(geometry_types - {"Polygon", "MultiPolygon"})
    if unsupported_types:
        names = ", ".join(unsupported_types)
        msg = f"Location areas require polygon geometry; received: {names}."
        raise ValueError(msg)
    if bool(gdf.geometry.is_empty.any()):
        msg = "Location areas require non-empty polygon geometry."
        raise ValueError(msg)
    if not bool(gdf.geometry.is_valid.all()):
        msg = "Location areas require valid polygon geometry."
        raise ValueError(msg)

    try:
        projected = gdf.to_crs(AREA_CRS)
    except (RuntimeError, ValueError) as exc:
        msg = f"Location geometry could not be projected to {AREA_CRS}."
        raise ValueError(msg) from exc

    areas = [float(area) for area in projected.geometry.area]
    if any(not math.isfinite(area) or area <= 0 for area in areas):
        msg = "Location areas must be finite and greater than zero square metres."
        raise ValueError(msg)
    return dict(zip(feature_ids, areas, strict=True))
