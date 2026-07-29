"""Optional PostgreSQL implementation of the Lyra plugin database interface."""

import math
from collections.abc import Sequence
from importlib import import_module
from typing import Literal

try:
    import geopandas
    from sqlalchemy import Connection, bindparam, func, literal_column, select
    from sqlalchemy.engine import Engine

    import_module("psycopg")
except ModuleNotFoundError as error:
    if error.name is None or error.name.split(".", maxsplit=1)[0] not in {
        "geopandas",
        "psycopg",
        "sqlalchemy",
    }:
        raise
    message = (
        "PostgreSQL support requires the 'postgres' extra. "
        "Install it with `uv add 'lyra-sdk[postgres]'`."
    )
    raise ModuleNotFoundError(message, name=error.name) from None

from lyra.sdk.db import LyraDB
from lyra.sdk.db_types import Bounds
from lyra.sdk.postgres_sql import (
    DEFAULT_POSTGRES_SCHEMA,
    compile_postgres_query,
    postgres_table,
    validate_postgres_identifier,
)

_DENUE_YEARS = frozenset({2020, 2021, 2022, 2023, 2024, 2025})
_DENUE_MONTHS = frozenset({5, 11})
_MESH_LEVELS = frozenset({4, 5, 6, 7, 8, 9})
_CENSUS_LEVELS = frozenset({"ent", "mun", "loc", "ageb", "mza"})


def _validate_bounds(bounds: Bounds) -> dict[str, float]:
    try:
        values = {
            "xmin": float(bounds.xmin),
            "ymin": float(bounds.ymin),
            "xmax": float(bounds.xmax),
            "ymax": float(bounds.ymax),
        }
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        msg = "bounds coordinates must be finite numbers"
        raise ValueError(msg) from exc
    if not all(math.isfinite(value) for value in values.values()):
        msg = "bounds coordinates must all be finite"
        raise ValueError(msg)
    if values["xmin"] >= values["xmax"]:
        msg = "bounds xmin must be less than xmax"
        raise ValueError(msg)
    if values["ymin"] >= values["ymax"]:
        msg = "bounds ymin must be less than ymax"
        raise ValueError(msg)
    return values


def _validate_columns(columns: Sequence[str]) -> list[str]:
    try:
        column_values = [columns] if isinstance(columns, str) else list(columns)
    except TypeError as exc:
        msg = "census columns must be a sequence of identifiers"
        raise ValueError(msg) from exc
    validated = [
        validate_postgres_identifier(column, field_name="census column")
        for column in column_values
    ]
    if len(validated) != len(set(validated)):
        msg = "census columns must be unique"
        raise ValueError(msg)
    if "geometry" not in validated:
        validated.append("geometry")
    return validated


def _load_geometries_from_bounds(
    *,
    bounds_parameters: dict[str, float],
    conn: Connection,
    columns: Sequence[str],
    table_name: str,
    schema: str,
) -> geopandas.GeoDataFrame:
    """Load geometries from a PostGIS table that intersect a bounding box.

    Returns:
        A GeoDataFrame of rows whose geometries intersect the given envelope.

    """
    table = postgres_table(
        table_name,
        schema=schema,
        columns=[*columns, "geometry"],
    )
    selected_columns = [table.c[column] for column in columns]
    geometry = table.c.geometry
    statement = (
        select(*selected_columns)
        .where(
            func.ST_Intersects(
                geometry,
                func.ST_MakeEnvelope(
                    bindparam("xmin"),
                    bindparam("ymin"),
                    bindparam("xmax"),
                    bindparam("ymax"),
                    literal_column("6372"),
                ),
            )
        )
        .order_by(
            *(table.c[column] for column in columns if column != "geometry"),
            func.ST_AsEWKB(geometry),
        )
    )
    return geopandas.read_postgis(
        compile_postgres_query(statement, conn),
        conn,
        params=bounds_parameters,
        geom_col="geometry",
    )


class PostgresLyraDB(LyraDB):
    """Implement the plugin database API using an injected synchronous engine."""

    def __init__(
        self,
        engine: Engine,
        *,
        schema: str = DEFAULT_POSTGRES_SCHEMA,
    ) -> None:
        """Initialize database operations with a runtime-owned engine."""
        self._schema = validate_postgres_identifier(
            schema,
            field_name="database schema",
        )
        self._engine = engine

    def load_denue_from_bounds(
        self,
        bounds: Bounds,
        *,
        year: Literal[2020, 2021, 2022, 2023, 2024, 2025],
        month: Literal[5, 11],
    ) -> geopandas.GeoDataFrame:
        """Load DENUE economic-unit records that intersect a bounding box.

        DENUE (Directorio Estadístico Nacional de Unidades Económicas) tables are
        named ``denue_{year}_{month:02d}``. Returns the columns ``per_ocu``
        (employment size), ``codigo_act`` (activity code), and ``geometry``.

        Args:
            bounds: Minimum and maximum x/y coordinates to query.
            year: Edition year of the DENUE dataset.
            month: Edition month of the DENUE dataset; either ``5`` (May) or
                ``11`` (November). Defaults to ``11``.

        Returns:
            A GeoDataFrame with columns ``["per_ocu", "codigo_act", "geometry"]``.

        Raises:
            ValueError: If the edition or bounds are invalid.

        """
        if (
            not isinstance(year, int)
            or isinstance(year, bool)
            or year not in _DENUE_YEARS
        ):
            msg = f"unsupported DENUE year: {year!r}"
            raise ValueError(msg)
        if (
            not isinstance(month, int)
            or isinstance(month, bool)
            or month not in _DENUE_MONTHS
        ):
            msg = f"unsupported DENUE month: {month!r}"
            raise ValueError(msg)
        parameters = _validate_bounds(bounds)

        with self._engine.connect() as conn:
            return _load_geometries_from_bounds(
                conn=conn,
                columns=["per_ocu", "codigo_act", "geometry"],
                table_name=f"denue_{year}_{month:02d}",
                schema=self._schema,
                bounds_parameters=parameters,
            )

    def load_mesh_from_bounds(
        self,
        bounds: Bounds,
        *,
        level: Literal[4, 5, 6, 7, 8, 9] = 9,
    ) -> geopandas.GeoDataFrame:
        """Load mesh-grid cells that intersect a bounding box.

        Queries the ``mesh_level_{level}`` table and returns cells with their
        ``codigo`` identifier and geometry.

        Args:
            bounds: Minimum and maximum x/y coordinates to query.
            level: Mesh resolution level (4-9). Higher values are finer.
                Defaults to ``9``.

        Returns:
            A GeoDataFrame with columns ``["codigo", "geometry"]``.

        Raises:
            ValueError: If the mesh level or bounds are invalid.

        """
        if (
            not isinstance(level, int)
            or isinstance(level, bool)
            or level not in _MESH_LEVELS
        ):
            msg = f"unsupported mesh level: {level!r}"
            raise ValueError(msg)
        parameters = _validate_bounds(bounds)

        with self._engine.connect() as conn:
            return _load_geometries_from_bounds(
                conn=conn,
                columns=["codigo", "geometry"],
                table_name=f"mesh_level_{level}",
                schema=self._schema,
                bounds_parameters=parameters,
            )

    def load_census_from_bounds(
        self,
        bounds: Bounds,
        *,
        level: Literal["ent", "mun", "loc", "ageb", "mza"],
        columns: Sequence[str],
    ) -> geopandas.GeoDataFrame:
        """Load 2020 census records that intersect a bounding box.

        Queries the ``census_2020_{level}`` table for the specified geographic
        level and columns.

        Args:
            bounds: Minimum and maximum x/y coordinates to query.
            level: Geographic level of the census table. One of ``"ent"``
                (state), ``"mun"`` (municipality), ``"loc"`` (locality),
                ``"ageb"``, or ``"mza"`` (block).
            columns: Column names to select (``"geometry"`` is added if absent).

        Returns:
            A GeoDataFrame of census records intersecting the bounding box.

        Raises:
            ValueError: If the level, columns, or bounds are invalid.

        """
        if not isinstance(level, str) or level not in _CENSUS_LEVELS:
            msg = f"unsupported census level: {level!r}"
            raise ValueError(msg)
        validated_columns = _validate_columns(columns)
        parameters = _validate_bounds(bounds)

        with self._engine.connect() as conn:
            return _load_geometries_from_bounds(
                conn=conn,
                columns=validated_columns,
                table_name=f"census_2020_{level}",
                schema=self._schema,
                bounds_parameters=parameters,
            )


__all__ = ["DEFAULT_POSTGRES_SCHEMA", "PostgresLyraDB"]
