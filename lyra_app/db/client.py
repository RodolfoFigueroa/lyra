"""High-level database operations used by the Lyra service."""

from collections.abc import Sequence
from typing import Literal

import geopandas
from lyra.sdk.db import LyraDB
from lyra.sdk.db_types import Bounds
from sqlalchemy import quoted_name
from sqlalchemy.engine import Engine

from lyra_app.loaders.db import load_geometries_from_bounds


class LyraDBImplicit(LyraDB):
    """Implement the plugin database API using an injected synchronous engine."""

    def __init__(self, engine: Engine) -> None:
        """Initialize database operations with the worker-owned engine."""
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
            return load_geometries_from_bounds(
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
            return load_geometries_from_bounds(
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
            return load_geometries_from_bounds(
                bounds,
                conn=conn,
                columns=columns,
                table_name=f"census_2020_{level}",
            )
