from __future__ import annotations

from typing import Annotated

from lyra.sdk.models import GeoJSON
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

ExplicitLocationAPI = Annotated[GeoJSON, "REQUIRE_EXPLICIT_TYPE"]
ExplicitBoundsAPI = Annotated[GeoJSON, "REQUIRE_EXPLICIT_BOUNDS_TYPE"]

__all__ = [
    "ExplicitBoundsAPI",
    "ExplicitLocationAPI",
    "ExplicitLocationUnion",
    "WrapperDataTypeInfo",
    "get_wrapper_data_type_info",
]
