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


class PointGeometry(StrictBaseModel):
    type: Literal["Point"]
    coordinates: list[float]


class PolygonGeometry(StrictBaseModel):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]


class MultiPolygonGeometry(StrictBaseModel):
    type: Literal["MultiPolygon"]
    coordinates: list[list[list[list[float]]]]


class Feature(StrictBaseModel):
    id: str = Field(min_length=1)
    type: Literal["Feature"]
    geometry: PointGeometry | PolygonGeometry | MultiPolygonGeometry
    properties: dict[str, Any]


class GeoJSON(StrictBaseModel):
    type: Literal["FeatureCollection"]
    features: list[Feature] = Field(min_length=1)
    crs: CRS
