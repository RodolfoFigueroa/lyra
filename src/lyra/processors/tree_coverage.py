import geopandas as gpd
import ee
import geemap


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


def calculate(df: gpd.GeoDataFrame) -> dict:
    df = df.to_crs("EPSG:4326")

    bbox = ee.Geometry.BBox(*df.total_bounds)
    img = load_tree_coverage_img(bbox)

    features = geemap.geopandas_to_ee(df)
    computed = ee.data.computeFeatures(
        {
            "expression": (
                img.reduceRegions(features, reducer=ee.Reducer.sum(), scale=25)
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
