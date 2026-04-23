import ee
from lyra.functions.utils import reduce_ee_image_over_gdf_factory


def load_urbanized_area_img(bbox: ee.Geometry) -> ee.Image:
    return (
        ee.ImageCollection("JRC/GHSL/P2023A/GHS_BUILT_S")
        .select("built_surface")
        .filterBounds(bbox)
        .mean()
    )


calculate = reduce_ee_image_over_gdf_factory(
    load_urbanized_area_img, reducer=ee.Reducer.sum(), scale=100
)
