"""Utilities for reducing Earth Engine images over GeoDataFrame geometries."""

import re
from collections.abc import Callable, Iterator

import ee
import geemap
import geopandas as gpd
import pandas as pd
import shapely
from lyra.sdk.types import ExplicitLocationAPI
from lyra.utils.geometry import convert_geojson_to_gdf


def convert_polygon_to_ee(polygon: shapely.Polygon) -> ee.Geometry:
    """Convert a Shapely polygon to an Earth Engine Geometry.

    Args:
        polygon: A ``shapely.Polygon`` to convert.

    Returns:
        An ``ee.Geometry.Polygon`` built from the exterior coordinates of the
        input polygon.
    """
    return ee.Geometry.Polygon(list(polygon.exterior.coords))


def get_reducer_name(reducer: ee.Reducer) -> str:
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


def compute_gdf(
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
    features = geemap.geopandas_to_ee(gdf[["geometry"]].reset_index(names="orig_index"))
    computed = ee.data.computeFeatures(
        {
            "expression": (img.reduceRegions(features, reducer=reducer, scale=scale)),
            "fileFormat": "PANDAS_DATAFRAME",
        },
    )
    col_name = get_reducer_name(reducer)
    return computed.set_index("orig_index")[col_name]


def chunk_gdf(
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


def reduce_ee_image_over_gdf_factory(
    load_img_func: Callable[[ee.Geometry], ee.Image],
    *,
    reducer: ee.Reducer,
    scale: int,
) -> Callable[[ExplicitLocationAPI], dict[str, float]]:
    """Create a function that reduces an EE image over locations from an API request.

    The returned function loads an Earth Engine image clipped to the bounding
    box of the input locations, then applies ``reducer`` at the given ``scale``
    to each geometry. If the request payload exceeds Earth Engine's size limit,
    the GeoDataFrame is automatically split into chunks and results are
    concatenated.

    Args:
        load_img_func: Callable that accepts an ``ee.Geometry.BBox`` and
            returns the ``ee.Image`` to reduce.
        reducer: The ``ee.Reducer`` to apply over each geometry.
        scale: Spatial resolution in metres to use for the reduction.

    Returns:
        A function that accepts an ``ExplicitLocationAPI`` object and returns a
        ``dict`` mapping each feature's original index to its reducer value.
    """

    def _f(data: ExplicitLocationAPI) -> dict:
        df = convert_geojson_to_gdf(data)[["geometry"]].to_crs("EPSG:4326")

        bbox = ee.Geometry.BBox(*df.total_bounds)
        img = load_img_func(bbox)

        try:
            return compute_gdf(img, df, reducer=reducer, scale=scale).to_dict()
        except ee.EEException as e:
            if re.match(r"Request payload size exceeds the limit", str(e)):
                processed_chunks = [
                    compute_gdf(img, chunk, reducer=reducer, scale=scale)
                    for chunk in chunk_gdf(df)
                ]
                return pd.concat(processed_chunks).to_dict()
            raise

    return _f
