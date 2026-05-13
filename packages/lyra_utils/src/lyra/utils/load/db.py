from collections.abc import Sequence
from typing import Literal

import geopandas as gpd
from sqlalchemy import Connection, quoted_name, text


def load_geometries_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    conn: Connection,
    columns: Sequence[str],
    table_name: str,
) -> gpd.GeoDataFrame:
    if "geometry" not in columns:
        columns = [*list(columns), "geometry"]

    table_name = quoted_name(table_name, quote=True)
    return gpd.read_postgis(
        f"""
        SELECT {", ".join(columns)} FROM {table_name}
        WHERE ST_Intersects(
            geometry,
            ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 6372)
        )
        """,  # noqa: S608
        conn,
        params={
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
        },
        geom_col="geometry",
    )


def get_table_name_for_cvegeos(cvegeos: list[str]) -> str:
    cvegeo_lengths = {len(cvegeo) for cvegeo in cvegeos}

    if len(cvegeo_lengths) != 1:
        err = "All cvegeos must have the same length to determine the geographic level."
        raise ValueError(err)

    length_to_level_map = {2: "ent", 5: "mun", 9: "loc", 13: "ageb", 16: "mza"}
    level = length_to_level_map.get(cvegeo_lengths.pop())

    return quoted_name(f"census_2020_{level}", quote=True)


def load_geometries_from_cvegeos(
    cvegeos: list[str],
    *,
    conn: Connection,
) -> gpd.GeoDataFrame:
    table_name = get_table_name_for_cvegeos(cvegeos)

    return gpd.read_postgis(
        f"""
        SELECT cvegeo, geometry AS geometry
        FROM {table_name}
        WHERE cvegeo IN %(cvegeos)s
        """,  # noqa: S608
        conn,
        params={"cvegeos": tuple(cvegeos)},
        geom_col="geometry",
    ).set_index("cvegeo")  # ty: ignore[no-matching-overload]


def load_bounds_from_cvegeos(
    cvegeos: list[str],
    *,
    conn: Connection,
) -> gpd.GeoDataFrame:
    table_name = get_table_name_for_cvegeos(cvegeos)

    return gpd.read_postgis(
        f"""
        SELECT ST_Extent(geometry)::geometry AS geometry
        FROM {table_name}
        WHERE cvegeo IN %(cvegeos)s
        """,  # noqa: S608
        conn,
        params={"cvegeos": tuple(cvegeos)},
        geom_col="geometry",
    )  # ty: ignore[no-matching-overload]


# TODO: Import levels other than AGEB
def load_geometries_from_met_zone_code(
    code: str,
    *,
    conn: Connection,
) -> gpd.GeoDataFrame:
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


def load_bounds_from_met_zone_code(code: str, *, conn: Connection) -> gpd.GeoDataFrame:
    return gpd.read_postgis(
        """
        SELECT ST_Extent(census_2020_ageb.geometry)::geometry AS geometry
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
    ).set_index("cve_met")


def get_met_zone_code_from_name(
    name: str,
    *,
    conn: Connection,
) -> tuple[str, str] | None:
    """Return (cve_met, nom_met) for the closest matching metropolitan zone name.

    Uses PostgreSQL trigram similarity (pg_trgm extension required).
    Returns None if no zone exceeds the similarity threshold.

    Args:
        name: The (possibly misspelled) metropolitan zone name to search for.

    Returns:
        A tuple of (cve_met, nom_met) for the best match, or None.
    """
    result = conn.execute(
        text(
            """
            SELECT cve_met, nom_met FROM metropoli_2020
            WHERE similarity(nom_met, :name) > 0.3
            ORDER BY similarity(nom_met, :name) DESC
            LIMIT 1
            """,
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
    conn: Connection,
    year: Literal[2020, 2021, 2022, 2023, 2024, 2025],
    month: Literal[5, 11] = 11,
) -> gpd.GeoDataFrame:
    table_name = quoted_name(f"denue_{year}_{month:02d}", quote=True)
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        conn=conn,
        columns=["per_ocu", "codigo_act", "geometry"],
        table_name=table_name,
    )


def load_mesh_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    conn: Connection,
    level: Literal[4, 5, 6, 7, 8, 9] = 9,
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        conn=conn,
        columns=["codigo", "geometry"],
        table_name=f"mesh_level_{level}",
    )


def load_census_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    conn: Connection,
    level: Literal["ent", "mun", "loc", "ageb", "mza"],
    columns: Sequence[str],
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        conn=conn,
        columns=columns,
        table_name=f"census_2020_{level}",
    )
