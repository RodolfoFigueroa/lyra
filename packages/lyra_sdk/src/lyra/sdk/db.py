from abc import ABC, abstractmethod

from collections.abc import Sequence
from typing import Literal
import geopandas as gpd

class LyraDB(ABC):

    @abstractmethod
    def load_denue_from_bounds(self, xmin: float, ymin: float, xmax: float, ymax: float, *, year: Literal[2020, 2021, 2022, 2023, 2024, 2025], month: Literal[5, 11]=11) -> gpd.GeoDataFrame:
        """Load DENUE economic-unit records that intersect a bounding box.

        DENUE (Directorio Estadístico Nacional de Unidades Económicas) tables are
        named ``denue_{year}_{month:02d}``. Returns the columns ``per_ocu``
        (employment size), ``codigo_act`` (activity code), and ``geometry``.

        Args:
            xmin: Minimum x coordinate of the bounding box.
            ymin: Minimum y coordinate of the bounding box.
            xmax: Maximum x coordinate of the bounding box.
            ymax: Maximum y coordinate of the bounding box.
            conn: Active SQLAlchemy database connection.
            year: Edition year of the DENUE dataset.
            month: Edition month of the DENUE dataset; either ``5`` (May) or
                ``11`` (November). Defaults to ``11``.

        Returns:
            A GeoDataFrame with columns ``["per_ocu", "codigo_act", "geometry"]``.
        """
        ...

    @abstractmethod
    def load_mesh_from_bounds(self, xmin: float, ymin: float, xmax: float, ymax: float, *, level: Literal[4, 5, 6, 7, 8, 9]=9) -> gpd.GeoDataFrame:
        """Load mesh-grid cells that intersect a bounding box.

        Queries the ``mesh_level_{level}`` table and returns cells with their
        ``codigo`` identifier and geometry.

        Args:
            xmin: Minimum x coordinate of the bounding box.
            ymin: Minimum y coordinate of the bounding box.
            xmax: Maximum x coordinate of the bounding box.
            ymax: Maximum y coordinate of the bounding box.
            conn: Active SQLAlchemy database connection.
            level: Mesh resolution level (4-9). Higher values are finer.
                Defaults to ``9``.

        Returns:
            A GeoDataFrame with columns ``["codigo", "geometry"]``.
        """
        ...

    @abstractmethod
    def load_census_from_bounds(self, xmin: float, ymin: float, xmax: float, ymax: float, *, level: Literal['ent', 'mun', 'loc', 'ageb', 'mza'], columns: Sequence[str]) -> gpd.GeoDataFrame:
        """Load 2020 census records that intersect a bounding box.

        Queries the ``census_2020_{level}`` table for the specified geographic
        level and columns.

        Args:
            xmin: Minimum x coordinate of the bounding box.
            ymin: Minimum y coordinate of the bounding box.
            xmax: Maximum x coordinate of the bounding box.
            ymax: Maximum y coordinate of the bounding box.
            conn: Active SQLAlchemy database connection.
            level: Geographic level of the census table. One of ``"ent"``
                (state), ``"mun"`` (municipality), ``"loc"`` (locality),
                ``"ageb"``, or ``"mza"`` (block).
            columns: Column names to select (``"geometry"`` is added if absent).

        Returns:
            A GeoDataFrame of census records intersecting the bounding box.
        """
        ...