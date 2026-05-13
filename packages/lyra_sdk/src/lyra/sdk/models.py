from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class FeatureNoMultiPolygon(StrictBaseModel):
    id: str = Field(min_length=1)
    type: Literal["Feature"]
    geometry: PointGeometry | PolygonGeometry
    properties: dict[str, Any]


class GeoJSON(StrictBaseModel):
    type: Literal["FeatureCollection"]
    features: list[Feature] = Field(min_length=1)
    crs: CRS


class SingleGeoJSON(StrictBaseModel):
    type: Literal["FeatureCollection"]
    features: list[FeatureNoMultiPolygon] = Field(min_length=1, max_length=1)
    crs: CRS
