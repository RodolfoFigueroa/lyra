import ee
from lyra.processors.common import reduce_ee_image_over_gdf_factory


def load_tree_coverage_img(bbox: ee.Geometry) -> ee.Image:
    return (
        ee.ImageCollection(
            "projects/sat-io/open-datasets/facebook/meta-canopy-height",
        )
        .filterBounds(bbox)
        .mean()
        .gte(ee.Number(3))
        .multiply(ee.image.Image.pixelArea())
    )


calculate = reduce_ee_image_over_gdf_factory(
    load_tree_coverage_img, reducer=ee.Reducer.sum(), scale=25
)
