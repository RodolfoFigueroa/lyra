"""Utilities for reducing Earth Engine images over GeoDataFrame geometries."""

import json
import re
from collections.abc import Iterator

import ee
import geopandas as gpd
import pandas as pd
import shapely


def convert_polygon_to_ee(polygon: shapely.Polygon) -> ee.Geometry:
    """Convert a Shapely polygon to an Earth Engine Geometry.

    Args:
        polygon: A ``shapely.Polygon`` to convert.

    Returns:
        An ``ee.Geometry.Polygon`` built from the exterior coordinates of the
        input polygon.

    """
    return ee.Geometry.Polygon(list(polygon.exterior.coords))


def convert_gdf_to_ee(gdf: gpd.GeoDataFrame) -> ee.FeatureCollection:
    """Convert a GeoDataFrame to an Earth Engine FeatureCollection.

    Args:
        gdf: A ``geopandas.GeoDataFrame`` to convert.

    Returns:
        An ``ee.FeatureCollection`` built from the input GeoDataFrame.

    Raises:
        ValueError: If the GeoDataFrame does not use EPSG:4326.

    """
    if not gdf.crs or gdf.crs.to_epsg() != 4326:
        err = "GeoDataFrame must be in EPSG:4326 (WGS84)"
        raise ValueError(err)

    return ee.FeatureCollection(json.loads(gdf.to_json()))


def _get_reducer_name(reducer: ee.Reducer) -> str:
    """Extract the reducer name from an Earth Engine Reducer object.

    ``ee.Reducer`` objects do not expose their name via a public API, but it
    is embedded in their string representation (e.g. ``"Reducer.mean"``).

    Args:
        reducer: An ``ee.Reducer`` instance.

    Returns:
        The name of the reducer (e.g. ``"mean"``, ``"sum"``).

    Raises:
        ValueError: If the reducer name cannot be parsed from the string
            representation.

    """
    # ee.Reducer objects don't have a public method to get their name, but the
    # name is included in the string representation.
    match = re.search(r"Reducer\.(\w+)", str(reducer))
    if match:
        return match.group(1)

    err = f"Could not extract reducer name from: {reducer}"
    raise ValueError(err)


def _compute_gdf(
    img: ee.Image,
    gdf: gpd.GeoDataFrame,
    *,
    reducer: ee.Reducer,
    scale: float,
) -> pd.Series:
    """Reduce an Earth Engine image over each geometry in a GeoDataFrame.

    Projects the GeoDataFrame geometries to Earth Engine as features, runs
    ``reduceRegions``, and returns the reducer output as a Series indexed by
    the original GeoDataFrame index.

    Args:
        img: The Earth Engine image to reduce.
        gdf: GeoDataFrame whose geometries define the reduction regions.
        reducer: The ``ee.Reducer`` to apply (e.g. ``ee.Reducer.mean()``).
        scale: Spatial resolution in metres to use for the reduction.

    Returns:
        A ``pd.Series`` indexed by the original GeoDataFrame index, containing
        the reducer value for each geometry.

    """
    features = convert_gdf_to_ee(gdf[["geometry"]].reset_index(names="orig_index"))
    computed = ee.data.computeFeatures(
        {
            "expression": (img.reduceRegions(features, reducer=reducer, scale=scale)),
            "fileFormat": "PANDAS_DATAFRAME",
        },
    )
    col_name = _get_reducer_name(reducer)
    return computed.set_index("orig_index")[col_name]


def _chunk_gdf(
    gdf: gpd.GeoDataFrame,
    chunk_size: int = 1000,
) -> Iterator[gpd.GeoDataFrame]:
    """Yield successive row-slices of a GeoDataFrame.

    Args:
        gdf: The GeoDataFrame to split.
        chunk_size: Maximum number of rows per chunk. Defaults to ``1000``.

    Yields:
        GeoDataFrame slices of at most ``chunk_size`` rows each.

    """
    for i in range(0, len(gdf), chunk_size):
        yield gdf.iloc[i : i + chunk_size]


def reduce_ee_image_over_gdf(
    gdf: gpd.GeoDataFrame, img: ee.Image, *, reducer: ee.Reducer, scale: float
) -> pd.Series:
    """Reduce an Earth Engine image over the geometries in a GeoDataFrame.

    Args:
        gdf: The GeoDataFrame whose geometries define the reduction regions.
        img: The Earth Engine image to reduce.
        reducer: The ``ee.Reducer`` to apply (e.g. ``ee.Reducer.mean()``).
        scale: Spatial resolution in metres to use for the reduction.

    Returns:
        A pandas Series with the reducer values for each geometry.

    Raises:
        ee.EEException: If the Earth Engine request fails for reasons other than
            exceeding the request payload size limit.
    """
    gdf = gdf[["geometry"]].to_crs("EPSG:4326")

    try:
        return _compute_gdf(img, gdf, reducer=reducer, scale=scale)
    except ee.EEException as e:
        if str(e).startswith(r"Request payload size exceeds the limit"):
            processed_chunks = [
                _compute_gdf(img, chunk, reducer=reducer, scale=scale)
                for chunk in _chunk_gdf(gdf)
            ]
            return pd.concat(processed_chunks)
        raise
