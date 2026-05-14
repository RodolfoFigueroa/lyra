from abc import ABC, abstractmethod

from lyra.sdk.models import GeoJSON


class LyraDB(ABC):
    @abstractmethod
    def load_denue(self, data: GeoJSON, *, year: int) -> GeoJSON: ...

    @abstractmethod
    def load_mesh(self, data: GeoJSON, *, buffer_size: float = 10000) -> GeoJSON: ...

    @abstractmethod
    def load_census(
        self,
        data: GeoJSON,
        *,
        columns: list[str],
        buffer_size: float = 10000,
    ) -> GeoJSON: ...
