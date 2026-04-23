from pydantic import BaseModel, Field, ConfigDict, AfterValidator
from typing import Annotated
from typing import Any, Literal


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CVEGEORequest(StrictBaseModel):
    cvegeo: list[str] = Field(min_length=1)


class CRSProperties(StrictBaseModel):
    name: str = Field(min_length=1)


class CRS(StrictBaseModel):
    type: Literal["name"]
    properties: CRSProperties


class GeoJSON(StrictBaseModel):
    type: Literal["FeatureCollection"]
    features: list[dict[str, Any]] = Field(min_length=1)
    crs: CRS


def validate_cvegeos(
    value: list[str],
):
    unique_lens = set(len(x) for x in value)
    if len(unique_lens) > 1:
        err = "All CVEGEO strings must have the same length."
        raise ValueError(err)

    allowed_lens = {2, 5, 9, 13, 16}
    found_len = unique_lens.pop()
    if found_len not in allowed_lens:
        err = f"CVEGEO strings must have length in {allowed_lens}, but got length {found_len}."
        raise ValueError(err)

    return value


class GeoJSONWrapper(StrictBaseModel):
    data_type: Literal["geojson"]
    value: GeoJSON


class CVEGEOListWrapper(StrictBaseModel):
    data_type: Literal["cvegeo_list"]
    value: Annotated[list[str], AfterValidator(validate_cvegeos)]


class MetZoneNameWrapper(StrictBaseModel):
    data_type: Literal["met_zone_name"]
    value: str = Field(min_length=1)


ExplicitInputUnion = Annotated[
    GeoJSONWrapper | CVEGEOListWrapper | MetZoneNameWrapper,
    Field(discriminator="data_type"),
]

ExplicitLocationAPI = Annotated[GeoJSON, "REQUIRE_EXPLICIT_TYPE"]
