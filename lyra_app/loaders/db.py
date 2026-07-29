"""Utilities for loading spatial data from a PostGIS database."""

from __future__ import annotations

from typing import TYPE_CHECKING

import geopandas
from lyra.sdk.postgres_sql import (
    DEFAULT_POSTGRES_SCHEMA,
    compile_postgres_query,
    postgres_table,
    validate_postgres_identifier,
)
from sqlalchemy import (
    Connection,
    String,
    bindparam,
    func,
    literal_column,
    select,
    union_all,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection
    from sqlalchemy.sql.schema import Table
    from sqlalchemy.sql.selectable import Join, Select, Subquery

_CVEGEO_LEVEL_BY_LENGTH = {
    2: "ent",
    5: "mun",
    9: "loc",
    13: "ageb",
    16: "mza",
}


def _validated_schema(schema: str) -> str:
    return validate_postgres_identifier(schema, field_name="database schema")


def get_table_name_for_cvegeos(cvegeos: list[str]) -> str:
    """Return the census table name for a non-empty, same-level CVEGEO list.

    Returns:
        The census table name corresponding to the identifiers' shared length.

    Raises:
        ValueError: If no identifiers are supplied, their lengths differ, or the
            shared length does not identify a supported census level.
    """
    if not cvegeos:
        msg = "cvegeos must contain at least one identifier"
        raise ValueError(msg)
    if any(not isinstance(cvegeo, str) or not cvegeo for cvegeo in cvegeos):
        msg = "cvegeos must contain only non-empty strings"
        raise ValueError(msg)
    cvegeo_lengths = {len(cvegeo) for cvegeo in cvegeos}
    if len(cvegeo_lengths) != 1:
        msg = "all cvegeos must belong to the same geographic level"
        raise ValueError(msg)
    length = next(iter(cvegeo_lengths))
    try:
        level = _CVEGEO_LEVEL_BY_LENGTH[length]
    except KeyError as exc:
        msg = f"unsupported cvegeo length: {length}"
        raise ValueError(msg) from exc
    return f"census_2020_{level}"


def _requested_cvegeos(cvegeos: list[str]) -> tuple[Subquery, dict[str, str]]:
    rows = []
    parameters = {}
    for position, cvegeo in enumerate(cvegeos):
        parameter_name = f"cvegeo_{position}"
        parameters[parameter_name] = cvegeo
        rows.append(
            select(
                bindparam(parameter_name, type_=String).label("cvegeo"),
                literal_column(str(position)).label("position"),
            )
        )
    return union_all(*rows).subquery("requested"), parameters


def load_geometries_from_cvegeos(
    cvegeos: list[str],
    *,
    conn: Connection,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> geopandas.GeoDataFrame:
    """Load census geometries in the same order as the requested CVEGEO codes.

    Returns:
        A GeoDataFrame indexed by ``cvegeo`` with a ``geometry`` column.
    """
    table_name = get_table_name_for_cvegeos(cvegeos)
    table = postgres_table(
        table_name,
        schema=_validated_schema(schema),
        columns=["cvegeo", "geometry"],
    )
    requested, parameters = _requested_cvegeos(cvegeos)
    statement = (
        select(
            requested.c.cvegeo,
            table.c.geometry.label("geometry"),
        )
        .select_from(requested.join(table, table.c.cvegeo == requested.c.cvegeo))
        .order_by(
            requested.c.position,
            table.c.cvegeo,
            func.ST_AsEWKB(table.c.geometry),
        )
    )
    return geopandas.read_postgis(
        compile_postgres_query(statement, conn),
        conn,
        params=parameters,
        geom_col="geometry",
    ).set_index("cvegeo")


def load_bounds_from_cvegeos(
    cvegeos: list[str],
    *,
    conn: Connection,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> geopandas.GeoDataFrame:
    """Load one aggregate bounding-box geometry for the requested CVEGEO codes.

    Returns:
        A single-row GeoDataFrame with the combined bounding-box geometry.
    """
    table_name = get_table_name_for_cvegeos(cvegeos)
    table = postgres_table(
        table_name,
        schema=_validated_schema(schema),
        columns=["cvegeo", "geometry"],
    )
    requested, parameters = _requested_cvegeos(cvegeos)
    statement = select(
        func.ST_Envelope(func.ST_Collect(table.c.geometry)).label("geometry")
    ).select_from(requested.join(table, table.c.cvegeo == requested.c.cvegeo))
    return geopandas.read_postgis(
        compile_postgres_query(statement, conn),
        conn,
        params=parameters,
        geom_col="geometry",
    )


def _metropolitan_tables(schema: str) -> tuple[Table, Table, Join]:
    validated_schema = _validated_schema(schema)
    ageb = postgres_table(
        "census_2020_ageb",
        schema=validated_schema,
        columns=["cvegeo", "cve_mun", "geometry"],
    )
    municipality = postgres_table(
        "census_2020_mun",
        schema=validated_schema,
        columns=["cvegeo", "cve_met"],
    )
    metropolitan = postgres_table(
        "metropoli_2020",
        schema=validated_schema,
        columns=["cve_met", "nom_met"],
    )
    joined = ageb.join(
        municipality,
        ageb.c.cve_mun == municipality.c.cvegeo,
    ).join(
        metropolitan,
        municipality.c.cve_met == metropolitan.c.cve_met,
    )
    return ageb, metropolitan, joined


def load_geometries_from_met_zone_code(
    code: str,
    *,
    conn: Connection,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> geopandas.GeoDataFrame:
    """Load AGEB geometries for all census units in a metropolitan zone.

    Returns:
        A GeoDataFrame of AGEB geometries indexed by ``cvegeo``.
    """
    ageb, metropolitan, joined = _metropolitan_tables(schema)
    statement = (
        select(ageb.c.cvegeo, ageb.c.geometry.label("geometry"))
        .select_from(joined)
        .where(metropolitan.c.cve_met == bindparam("code"))
        .order_by(
            ageb.c.cvegeo,
            func.ST_AsEWKB(ageb.c.geometry),
        )
    )
    return geopandas.read_postgis(
        compile_postgres_query(statement, conn),
        conn,
        params={"code": code},
        geom_col="geometry",
    ).set_index("cvegeo")


def load_bounds_from_met_zone_code(
    code: str,
    *,
    conn: Connection,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> geopandas.GeoDataFrame:
    """Load one aggregate bounding-box geometry for a metropolitan zone.

    Returns:
        A single-row GeoDataFrame containing the combined bounding-box geometry.
    """
    ageb, metropolitan, joined = _metropolitan_tables(schema)
    statement = (
        select(func.ST_Envelope(func.ST_Collect(ageb.c.geometry)).label("geometry"))
        .select_from(joined)
        .where(metropolitan.c.cve_met == bindparam("code"))
    )
    return geopandas.read_postgis(
        compile_postgres_query(statement, conn),
        conn,
        params={"code": code},
        geom_col="geometry",
    )


def _met_zone_lookup_statement(schema: str) -> Select[tuple[str, str]]:
    metropolitan = postgres_table(
        "metropoli_2020",
        schema=_validated_schema(schema),
        columns=["cve_met", "nom_met"],
    )
    score = func.similarity(metropolitan.c.nom_met, bindparam("name"))
    return (
        select(metropolitan.c.cve_met, metropolitan.c.nom_met)
        .where(score > bindparam("similarity_threshold"))
        .order_by(
            score.desc(),
            metropolitan.c.nom_met,
            metropolitan.c.cve_met,
        )
        .limit(literal_column("1"))
    )


def get_met_zone_code_from_name(
    name: str,
    *,
    conn: Connection,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> tuple[str, str] | None:
    """Return the closest metropolitan-zone code and name, if one matches."""
    result = conn.execute(
        _met_zone_lookup_statement(schema),
        {"name": name, "similarity_threshold": 0.3},
    )
    row = result.fetchone()
    if row is None:
        return None
    return row.cve_met, row.nom_met


async def get_met_zone_code_from_name_async(
    name: str,
    *,
    conn: AsyncConnection,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> tuple[str, str] | None:
    """Return the closest metropolitan-zone match using an async connection."""
    result = await conn.execute(
        _met_zone_lookup_statement(schema),
        {"name": name, "similarity_threshold": 0.3},
    )
    row = result.fetchone()
    if row is None:
        return None
    return row.cve_met, row.nom_met
