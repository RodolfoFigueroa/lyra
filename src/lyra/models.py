from pydantic import BaseModel, Field
from typing import Any, Literal


class CVEGEORequest(BaseModel):
    cvegeo: list[str] = Field(min_length=1)


class CRSProperties(BaseModel):
    name: str = Field(min_length=1)


class CRS(BaseModel):
    type: Literal["name"]
    properties: CRSProperties


class GeoJSON(BaseModel):
    type: Literal["FeatureCollection"]
    features: list[dict[str, Any]] = Field(min_length=1)
    crs: CRS


class GeoJSONRequest(BaseModel):
    geojson: GeoJSON


class AccessibilityGeoJSONRequest(BaseModel):
    geojson: GeoJSON
    geojson_public: GeoJSON


class JobAccessibilityGeoJSONRequest(BaseModel):
    geojson: GeoJSON
    group_patterns: list[str] | None = None
