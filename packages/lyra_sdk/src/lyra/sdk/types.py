from typing import Annotated

from lyra.sdk.models import GeoJSON, SingleGeoJSON, StrictBaseModel
from pydantic import Field, field_validator

REQUIRE_EXPLICIT_TYPE = "REQUIRE_EXPLICIT_TYPE"
REQUIRE_EXPLICIT_BOUNDS_TYPE = "REQUIRE_EXPLICIT_BOUNDS_TYPE"

DERIVE_DENUE = "DERIVE_DENUE"
DERIVE_MESH = "DERIVE_MESH"
DERIVE_CENSUS = "DERIVE_CENSUS"

ExplicitLocationAPI = Annotated[GeoJSON, REQUIRE_EXPLICIT_TYPE]
ExplicitBoundsAPI = Annotated[SingleGeoJSON, REQUIRE_EXPLICIT_BOUNDS_TYPE]

ALLOWED_CENSUS_COLS = ["pobtot"]


class DENUEDerivationParams(StrictBaseModel):
    buffer_size: float = Field(default=10000, ge=0)


class MeshDerivationParams(StrictBaseModel):
    buffer_size: float = Field(default=10000, ge=0)


class CensusDerivationParams(StrictBaseModel):
    buffer_size: float = Field(default=10000, ge=0)
    columns: list[str]

    @field_validator("columns")
    @classmethod
    def validate_columns(cls, v: list[str]) -> list[str]:
        invalid = set(v) - set(ALLOWED_CENSUS_COLS)
        if invalid:
            err = f"Invalid census columns: {invalid}. Allowed: {ALLOWED_CENSUS_COLS}"
            raise ValueError(err)
        return v


DerivedDENUEAPI = Annotated[GeoJSON, DERIVE_DENUE, DENUEDerivationParams()]
DerivedMeshAPI = Annotated[GeoJSON, DERIVE_MESH, MeshDerivationParams()]
