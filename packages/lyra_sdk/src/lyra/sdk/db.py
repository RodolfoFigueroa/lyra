from abc import ABC, abstractmethod
from typing import Literal

from lyra.sdk.models import GeoJSON


class LyraDB(ABC):
    @abstractmethod
    def load_denue(
        self,
        data: GeoJSON,
        *,
        year: Literal[2020, 2021, 2022, 2023, 2024, 2025],
        month: Literal[5, 11],
        buffer_size: float = 10_000,
    ) -> GeoJSON:
        """Load DENUE (business directory) points within the extent of *data*.

        Args:
            data: The reference geometry used to spatially filter results.
            year: The DENUE publication year to query.
            month: The DENUE publication month to query (5 = May, 11 = November).
            buffer_size: Buffer distance in metres applied around *data* before
                filtering. Must be >= 0. Defaults to 10000.

        Returns:
            A GeoJSON FeatureCollection of DENUE point features.
        """
        ...

    @abstractmethod
    def load_mesh(
        self, data: GeoJSON, *, buffer_size: float = 10_000, level: Literal[9] = 9,
    ) -> GeoJSON:
        """Load statistical mesh polygons within the extent of *data*.

        Args:
            data: The reference geometry used to spatially filter results.
            buffer_size: Buffer distance in metres applied around *data* before
                filtering. Must be >= 0. Defaults to 10000.
            level: Mesh resolution level. Defaults to 9.

        Returns:
            A GeoJSON FeatureCollection of mesh polygon features.
        """
        ...

    @abstractmethod
    def load_census(
        self,
        data: GeoJSON,
        *,
        columns: list[str],
        year: Literal[2010, 2020] = 2020,
        buffer_size: float = 10_000,
    ) -> GeoJSON:
        """Load census data within the extent of *data*.

        Args:
            data: The reference geometry used to spatially filter results.
            columns: Census variable names to include as feature properties.
                Only columns listed in ``ALLOWED_CENSUS_COLS`` are permitted.
            year: The census year to query. Defaults to 2020.
            buffer_size: Buffer distance in metres applied around *data* before
                filtering. Must be >= 0. Defaults to 10000.

        Returns:
            A GeoJSON FeatureCollection with the requested census columns as
            feature properties.
        """
        ...
