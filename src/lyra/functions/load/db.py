from sqlalchemy import quoted_name
import geopandas as gpd
from lyra.db import engine
from typing import Sequence, Literal
from lyra.models.base import GeoJSON
import json


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

        table_name = quoted_name(table_name, quote=True)

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


def load_geometries_from_cvegeos(
    cvegeos: list[str],
) -> gpd.GeoDataFrame:
    cvegeo_lengths = set(len(cvegeo) for cvegeo in cvegeos)

    length_to_level_map = {2: "ent", 5: "mun", 9: "loc", 13: "ageb", 16: "mza"}
    level = length_to_level_map.get(cvegeo_lengths.pop())

    table_name = quoted_name(f"census_2020_{level}", quote=True)

    with engine.connect() as conn:
        return gpd.read_postgis(
            f"""
            SELECT cvegeo, geometry AS geometry
            FROM {table_name}
            WHERE cvegeo IN %(cvegeos)s
            """,
            conn,
            params={"cvegeos": tuple(cvegeos)},
            geom_col="geometry",
        )  # ty:ignore[no-matching-overload]


def load_geojson_from_cvegeos(cvegeos: list[str]):
    gdf = load_geometries_from_cvegeos(cvegeos)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_geometries_from_met_zone_name(name: str) -> gpd.GeoDataFrame:
    return gpd.read_postgis(
        """
        SELECT census_2020_ageb.cvegeo, census_2020_ageb.geometry
        FROM census_2020_ageb
        """
    )


def load_geojson_from_met_zone_name(name: str) -> GeoJSON:
    gdf = load_geometries_from_met_zone_name(name)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_denue_from_bounds(
    xmin: float, ymin: float, xmax: float, ymax: float, *, table_name: str
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        columns=["per_ocu", "codigo_act", "geometry"],
        table_name=table_name,
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
