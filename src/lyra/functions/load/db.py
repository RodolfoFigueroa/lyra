import geopandas as gpd
from lyra.db import engine
from typing import Sequence, Literal


def load_geometries_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    columns: Sequence[str],
    table_name: str,
) -> gpd.GeoDataFrame:
    with engine.connect() as conn:
        if "geometry" not in columns:
            columns = list(columns) + ["geometry"]

        return gpd.read_postgis(
            f"""
            SELECT {", ".join(columns)} FROM {table_name}
            WHERE ST_Intersects(geometry, ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 6372))
            """,
            conn,
            params={
                "xmin": xmin,
                "ymin": ymin,
                "xmax": xmax,
                "ymax": ymax,
            },
            geom_col="geometry",
        )


def load_denue_from_bounds(
    xmin: float, ymin: float, xmax: float, ymax: float
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        columns=["per_ocu", "codigo_act", "geometry"],
        table_name="denue_05_2025",
    )


def load_mesh_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    level: Literal[4, 5, 6, 7, 8, 9] = 9,
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        columns=["codigo", "geometry"],
        table_name=f"mesh_level_{level}",
    )


def load_census_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    level: Literal["ent", "mun", "loc", "ageb", "mza"],
    columns: Sequence[str],
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        columns=columns,
        table_name=f"census_2020_{level}",
    )
