import sqlalchemy
import os
import geopandas as gpd


engine = sqlalchemy.engine.create_engine(
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)


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
