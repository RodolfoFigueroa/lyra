from pydantic import BaseModel, Field, ConfigDict
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
