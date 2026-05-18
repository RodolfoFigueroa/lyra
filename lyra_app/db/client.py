from lyra.sdk.models import GeoJSON


class LyraDBImplicit:
    def load_denue(self, data: GeoJSON, *, year: int) -> GeoJSON:
        """
        Test
        """
        raise NotImplementedError

    def load_mesh(self, data: GeoJSON, *, buffer_size: float = 10000) -> GeoJSON:
        raise NotImplementedError

    def load_census(
        self,
        data: GeoJSON,
        *,
        columns: list[str],
        buffer_size: float = 10000,
    ) -> GeoJSON:
        raise NotImplementedError
