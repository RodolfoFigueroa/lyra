from lyra.sdk.models import GeoJSON
from lyra.sdk.types import DERIVE_CENSUS, DERIVE_DENUE, DERIVE_MESH

DERIVATION_TAGS = {DERIVE_DENUE, DERIVE_MESH, DERIVE_CENSUS}


def load_derived_denue(data: GeoJSON, *, buffer_size: float = 10000) -> GeoJSON:
    raise NotImplementedError


def load_derived_mesh(data: GeoJSON, *, buffer_size: float = 10000) -> GeoJSON:
    raise NotImplementedError


def load_derived_census(
    data: GeoJSON,
    *,
    buffer_size: float = 10000,
    columns: list[str],
) -> GeoJSON:
    raise NotImplementedError


DERIVATION_MAP = {
    DERIVE_DENUE: load_derived_denue,
    DERIVE_MESH: load_derived_mesh,
    DERIVE_CENSUS: load_derived_census,
}
