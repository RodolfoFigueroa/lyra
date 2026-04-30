from sqlalchemy import quoted_name, text
import geopandas as gpd
from lyra.constants import YEAR_TO_DENUE_TABLE_MAP
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


# TODO: Import levels other than AGEB
def load_geometries_from_met_zone_code(code: str) -> gpd.GeoDataFrame:
    with engine.connect() as conn:
        return gpd.read_postgis(
            """
            SELECT census_2020_ageb.cvegeo, census_2020_ageb.geometry
                FROM census_2020_ageb
            INNER JOIN census_2020_mun
                ON census_2020_ageb.cve_mun = census_2020_mun.cvegeo
            INNER JOIN metropoli_2020
                ON census_2020_mun.cve_met = metropoli_2020.cve_met
            WHERE metropoli_2020.cve_met = %(code)s
            """,
            conn,
            params={"code": code},
            geom_col="geometry",
        ).set_index("cvegeo")


def load_geojson_from_met_zone_code(code: str) -> GeoJSON:
    gdf = load_geometries_from_met_zone_code(code)
    return GeoJSON(**json.loads(gdf.to_json()))


def get_met_zone_code_from_name(name: str) -> tuple[str, str] | None:
    """Return (cve_met, nom_met) for the closest matching metropolitan zone name.

    Uses PostgreSQL trigram similarity (pg_trgm extension required).
    Returns None if no zone exceeds the similarity threshold.

    Args:
        name: The (possibly misspelled) metropolitan zone name to search for.

    Returns:
        A tuple of (cve_met, nom_met) for the best match, or None.
    """
    # Requires: CREATE EXTENSION IF NOT EXISTS pg_trgm;
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT cve_met, nom_met FROM metropoli_2020
                WHERE similarity(nom_met, :name) > 0.3
                ORDER BY similarity(nom_met, :name) DESC
                LIMIT 1
                """
            ),
            {"name": name},
        )
        row = result.fetchone()
        if row is None:
            return None
        return row.cve_met, row.nom_met


def load_denue_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    year: Literal[2020, 2021, 2022, 2023, 2024, 2025],
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        columns=["per_ocu", "codigo_act", "geometry"],
        table_name=YEAR_TO_DENUE_TABLE_MAP[year],
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
