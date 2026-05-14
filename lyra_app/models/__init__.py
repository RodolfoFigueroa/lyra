from __future__ import annotations

from typing import Annotated

from pydantic import Field

from lyra_app.models.cvegeo_list import CVEGEOListWrapper
from lyra_app.models.geojson import GeoJSONWrapper, SingleGeoJSONWrapper
from lyra_app.models.met_zone_code import MetZoneCodeWrapper

ExplicitLocationUnion = Annotated[
    CVEGEOListWrapper | GeoJSONWrapper | MetZoneCodeWrapper,
    Field(discriminator="data_type"),
]
ExplicitBoundsUnion = Annotated[
    CVEGEOListWrapper | SingleGeoJSONWrapper | MetZoneCodeWrapper,
    Field(discriminator="data_type"),
]

__all__ = [
    "ExplicitBoundsUnion",
    "ExplicitLocationUnion",
]
