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
    ) -> GeoJSON: ...

    @abstractmethod
    def load_mesh(
        self, data: GeoJSON, *, buffer_size: float = 10_000, level: Literal[9] = 9,
    ) -> GeoJSON: ...

    @abstractmethod
    def load_census(
        self,
        data: GeoJSON,
        *,
        columns: list[str],
        year: Literal[2010, 2020] = 2020,
        buffer_size: float = 10_000,
    ) -> GeoJSON: ...
