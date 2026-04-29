import pandas as pd
import pandana as pdna
import ee
import geemap
from typing import Callable
import geopandas as gpd
from lyra.models.base import GeoJSON
from lyra.models.wrappers import ExplicitLocationAPI


def convert_geojson_to_gdf(geojson: GeoJSON) -> gpd.GeoDataFrame:
    out = gpd.GeoDataFrame.from_features(
        [feature.model_dump(mode="json") for feature in geojson.features],
        crs=geojson.crs.properties.name,
    )
    out.index = [feature.id for feature in geojson.features]

    return out


def reduce_ee_image_over_gdf_factory(
    load_img_func: Callable[[ee.Geometry], ee.Image], *, reducer: ee.Reducer, scale: int
) -> Callable[[ExplicitLocationAPI], dict[str, float]]:
    def _f(data: ExplicitLocationAPI) -> dict:
        df = convert_geojson_to_gdf(data)[["geometry"]].reset_index(names="orig_index").to_crs("EPSG:4326")

        bbox = ee.Geometry.BBox(*df.total_bounds)
        img = load_img_func(bbox)

        features = geemap.geopandas_to_ee(df)
        computed = ee.data.computeFeatures(
            {
                "expression": (
                    img.reduceRegions(features, reducer=reducer, scale=scale)
                ),
                "fileFormat": "PANDAS_DATAFRAME",
            },
        )

        # TODO: Temporary fix until ty respects annotated over inferred types
        return pd.DataFrame(computed[["orig_index", "sum"]]).set_index("orig_index")["sum"].to_dict()

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
