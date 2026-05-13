import calendar
import re
from collections.abc import Callable, Iterator
from typing import Literal

import ee
import geemap
import geopandas as gpd
import pandana as pdna
import pandas as pd
import shapely
from lyra.sdk.models import GeoJSON
from lyra.sdk.types import ExplicitLocationAPI


def convert_geojson_to_gdf(geojson: GeoJSON) -> gpd.GeoDataFrame:
    out = gpd.GeoDataFrame.from_features(
        [feature.model_dump(mode="json") for feature in geojson.features],
        crs=geojson.crs.properties.name,
    )
    out.index = [feature.id for feature in geojson.features]

    return out


def get_reducer_name(reducer: ee.Reducer) -> str:
    # ee.Reducer objects don't have a public method to get their name, but the
    # name is included in the string representation.
    match = re.search(r"Reducer\.(\w+)", str(reducer))
    if match:
        return match.group(1)

    err = f"Could not extract reducer name from: {reducer}"
    raise ValueError(err)


def chunk_gdf(
    gdf: gpd.GeoDataFrame,
    chunk_size: int = 1000,
) -> Iterator[gpd.GeoDataFrame]:
    for i in range(0, len(gdf), chunk_size):
        yield gdf.iloc[i : i + chunk_size]


def compute_gdf(
    img: ee.Image,
    gdf: gpd.GeoDataFrame,
    *,
    reducer: ee.Reducer,
    scale: float,
) -> pd.Series:
    features = geemap.geopandas_to_ee(gdf[["geometry"]].reset_index(names="orig_index"))
    computed = ee.data.computeFeatures(
        {
            "expression": (img.reduceRegions(features, reducer=reducer, scale=scale)),
            "fileFormat": "PANDAS_DATAFRAME",
        },
    )
    col_name = get_reducer_name(reducer)
    return computed.set_index("orig_index")[col_name]


def reduce_ee_image_over_gdf_factory(
    load_img_func: Callable[[ee.Geometry], ee.Image],
    *,
    reducer: ee.Reducer,
    scale: int,
) -> Callable[[ExplicitLocationAPI], dict[str, float]]:
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


def get_geometries_osmid(
    geometries: gpd.GeoDataFrame,
    net_accessibility: pdna.Network,
    *,
    mapping_distance: float = 1000,
) -> pd.Series:
    return net_accessibility.get_node_ids(
        x_col=geometries["geometry"].centroid.x,
        y_col=geometries["geometry"].centroid.y,
        # Despite what pandana documentation says, this mapping distance is
        # just standard Euclidean, not based on network impedance. Thus, we
        # don't need to scale it.
        mapping_distance=mapping_distance,
    )


def get_date_range(month: int, year: int) -> tuple[str, str]:
    month_str = str(month).rjust(2, "0")

    start = f"{year}-{month_str}-01"

    _, end_day = calendar.monthrange(year, month)
    end_day_str = str(end_day).rjust(2, "0")
    end = f"{year}-{month_str}-{end_day_str}"

    return start, end


def get_season_date_range(
    season: Literal["winter", "spring", "summer", "autumn"],
    year: int,
) -> tuple[str, str]:
    if season == "winter":
        start, _ = get_date_range(12, year - 1)
        _, end = get_date_range(2, year)
    elif season == "spring":
        start, _ = get_date_range(3, year)
        _, end = get_date_range(5, year)
    elif season == "summer":
        start, _ = get_date_range(6, year)
        _, end = get_date_range(8, year)
    elif season == "autumn":
        start, _ = get_date_range(9, year)
        _, end = get_date_range(11, year)
    else:
        err = (
            f"Invalid season: {season}. Must be one of 'winter', 'spring', "
            "'summer', or 'autumn'."
        )
        raise ValueError(err)

    return start, end


def convert_polygon_to_ee(polygon: shapely.Polygon) -> ee.Geometry:
    return ee.Geometry.Polygon(list(polygon.exterior.coords))
