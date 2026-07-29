"""Optional PostgreSQL implementation of the Lyra plugin database interface."""

from collections.abc import Sequence
from importlib import import_module
from typing import Literal

try:
    import geopandas
    from sqlalchemy import Connection, quoted_name
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


def _load_geometries_from_bounds(
    bounds: Bounds,
    *,
    conn: Connection,
    columns: Sequence[str],
    table_name: str,
) -> geopandas.GeoDataFrame:
    """Load geometries from a PostGIS table that intersect a bounding box.

    Returns:
        A GeoDataFrame of rows whose geometries intersect the given envelope.

    """
    if "geometry" not in columns:
        columns = [*list(columns), "geometry"]

    table_name = quoted_name(table_name, quote=True)
    return geopandas.read_postgis(
        f"""
        SELECT {", ".join(columns)} FROM {table_name}
        WHERE ST_Intersects(
            geometry,
            ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 6372)
        )
        """,  # ruff:ignore[hardcoded-sql-expression]
        conn,
        params={
            "xmin": float(bounds.xmin),
            "ymin": float(bounds.ymin),
            "xmax": float(bounds.xmax),
            "ymax": float(bounds.ymax),
        },
        geom_col="geometry",
    )


class PostgresLyraDB(LyraDB):
    """Implement the plugin database API using an injected synchronous engine."""

    def __init__(self, engine: Engine) -> None:
        """Initialize database operations with a runtime-owned engine."""
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

        """
        table_name = quoted_name(f"denue_{year}_{month:02d}", quote=True)

        with self._engine.connect() as conn:
            return _load_geometries_from_bounds(
                bounds,
                conn=conn,
                columns=["per_ocu", "codigo_act", "geometry"],
                table_name=table_name,
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

        """
        with self._engine.connect() as conn:
            return _load_geometries_from_bounds(
                bounds,
                conn=conn,
                columns=["codigo", "geometry"],
                table_name=f"mesh_level_{level}",
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

        """
        with self._engine.connect() as conn:
            return _load_geometries_from_bounds(
                bounds,
                conn=conn,
                columns=columns,
                table_name=f"census_2020_{level}",
            )


__all__ = ["PostgresLyraDB"]
