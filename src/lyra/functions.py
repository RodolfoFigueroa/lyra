import geopandas as gpd
import ee
import os
import sqlalchemy
import geemap

engine = sqlalchemy.engine.create_engine(
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)


ee.Initialize(project=os.environ["EARTHENGINE_PROJECT"])


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


def calculate(df: gpd.GeoDataFrame) -> dict[str, float]:
    bbox = ee.Geometry.BBox(*df.total_bounds)
    img = load_tree_coverage_img(bbox)

    reducer = ee.Reducer.sum()
    scale = 25

    features = geemap.geopandas_to_ee(df)
    computed = ee.data.computeFeatures(
        {
            "expression": (img.reduceRegions(features, reducer=reducer, scale=scale)),
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


def calculate_two(df: gpd.GeoDataFrame) -> dict[str, float]:
    return {"a": 1.0}


def get_gdf_from_cvegeo(
    cvegeos: list,
    table_name: str,
) -> gpd.GeoDataFrame:
    with engine.connect() as conn:
        return gpd.read_postgis(
            f"""
                SELECT cvegeo, ST_Transform(geometry, 4326) AS geometry
                FROM {table_name}
                WHERE cvegeo IN %(cvegeos)s
                """,
            conn,
            params={"cvegeos": tuple(cvegeos)},
            geom_col="geometry",
        )


endpoint_map = {"tree_coverage": calculate, "urban_area": calculate_two}
