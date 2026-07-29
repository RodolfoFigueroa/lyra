"""Database access helpers exposed to plugin code."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Literal

    import geopandas
    from lyra.sdk.db_types import Bounds


class LyraDatabaseError(RuntimeError):
    """Base class for stable, public database-service failures."""

    code = "database_error"
    retryable = False
    public_message = "The database operation failed."

    def __init__(self) -> None:
        """Initialize the failure with its safe public message."""
        super().__init__(self.public_message)


class DatabaseNotConfiguredError(LyraDatabaseError):
    """Raised when database access is attempted without an implementation."""

    code = "database_not_configured"
    public_message = "Database access is not configured."

    def __init__(self, operation: str) -> None:
        """Describe the unavailable database operation.

        Args:
            operation: Name of the :class:`LyraDB` method that was called.

        """
        self.operation = operation
        RuntimeError.__init__(
            self,
            f"Database operation {operation!r} requires a configured LyraDB. "
            "Supply a real or fake LyraDB implementation.",
        )


class DatabaseUnavailableError(LyraDatabaseError):
    """Indicate a transient connection, capacity, or server availability failure."""

    code = "database_unavailable"
    retryable = True
    public_message = "The database is temporarily unavailable."


class DatabaseQueryTimeoutError(LyraDatabaseError):
    """Indicate that a database statement exceeded its execution deadline."""

    code = "database_query_timeout"
    retryable = True
    public_message = "The database query timed out."


class DatabaseQueryError(LyraDatabaseError):
    """Indicate a non-transient database execution or schema failure."""

    code = "database_query_error"
    public_message = "The database query failed."


class LyraDB(ABC):
    """Define the read-only database operations available to plugin code.

    Concrete implementations own their initialization and resource requirements.
    The interface deliberately does not prescribe an engine or connection lifecycle.
    """

    @abstractmethod
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
        ...

    @abstractmethod
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
        ...

    @abstractmethod
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
        ...


class StubLyraDB(LyraDB):
    """Reject every database operation until a real or fake client is supplied."""

    def load_denue_from_bounds(
        self,
        bounds: Bounds,
        *,
        year: Literal[2020, 2021, 2022, 2023, 2024, 2025],
        month: Literal[5, 11],
    ) -> geopandas.GeoDataFrame:
        """Reject DENUE access.

        Raises:
            DatabaseNotConfiguredError: Always, because no database is configured.

        """
        del bounds, year, month
        operation = self.load_denue_from_bounds.__name__
        raise DatabaseNotConfiguredError(operation)

    def load_mesh_from_bounds(
        self,
        bounds: Bounds,
        *,
        level: Literal[4, 5, 6, 7, 8, 9] = 9,
    ) -> geopandas.GeoDataFrame:
        """Reject mesh access.

        Raises:
            DatabaseNotConfiguredError: Always, because no database is configured.

        """
        del bounds, level
        operation = self.load_mesh_from_bounds.__name__
        raise DatabaseNotConfiguredError(operation)

    def load_census_from_bounds(
        self,
        bounds: Bounds,
        *,
        level: Literal["ent", "mun", "loc", "ageb", "mza"],
        columns: Sequence[str],
    ) -> geopandas.GeoDataFrame:
        """Reject census access.

        Raises:
            DatabaseNotConfiguredError: Always, because no database is configured.

        """
        del bounds, level, columns
        operation = self.load_census_from_bounds.__name__
        raise DatabaseNotConfiguredError(operation)


__all__ = [
    "DatabaseNotConfiguredError",
    "DatabaseQueryError",
    "DatabaseQueryTimeoutError",
    "DatabaseUnavailableError",
    "LyraDB",
    "LyraDatabaseError",
    "StubLyraDB",
]
