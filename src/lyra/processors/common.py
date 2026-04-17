import geemap
import ee
from typing import Callable
import geopandas as gpd


def reduce_ee_image_over_gdf_factory(
    load_img_func: Callable[[ee.Geometry], ee.Image], *, reducer: ee.Reducer, scale: int
) -> Callable[[gpd.GeoDataFrame], dict]:
    def _f(df: gpd.GeoDataFrame) -> dict:
        df = df.to_crs("EPSG:4326")

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
        gdf = gpd.GeoDataFrame(computed)
        return (
            gdf[["cvegeo", "sum"]]
            .rename(columns={"sum": "area_m2"})
            .set_index("cvegeo")["area_m2"]
            .to_dict()
        )

    return _f
