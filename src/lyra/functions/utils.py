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
        df = convert_geojson_to_gdf(data)
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
