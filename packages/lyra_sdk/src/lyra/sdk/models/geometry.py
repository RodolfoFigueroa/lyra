from typing import Any, Literal

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class CRSProperties(StrictBaseModel):
    """Coordinate reference system properties."""

    name: str = Field(min_length=1, description="CRS identifier such as EPSG:4326.")


class CRS(StrictBaseModel):
    """GeoJSON coordinate reference system object."""

    type: Literal["name"] = Field(description="CRS object type.")
    properties: CRSProperties = Field(description="CRS properties.")


class PointGeometry(StrictBaseModel):
    """GeoJSON point geometry."""

    type: Literal["Point"] = Field(description="GeoJSON geometry type.")
    coordinates: list[float] = Field(description="Point coordinate pair.")


class PolygonGeometry(StrictBaseModel):
    """GeoJSON polygon geometry."""

    type: Literal["Polygon"] = Field(description="GeoJSON geometry type.")
    coordinates: list[list[list[float]]] = Field(
        description="Polygon rings and coordinate pairs.",
    )


class MultiPolygonGeometry(StrictBaseModel):
    """GeoJSON multi-polygon geometry."""

    type: Literal["MultiPolygon"] = Field(description="GeoJSON geometry type.")
    coordinates: list[list[list[list[float]]]] = Field(
        description="Multi-polygon rings and coordinate pairs.",
    )


class Feature(StrictBaseModel):
    """GeoJSON feature that may contain point, polygon, or multi-polygon geometry."""

    id: str = Field(min_length=1, description="Stable feature identifier.")
    type: Literal["Feature"] = Field(description="GeoJSON feature type.")
    geometry: PointGeometry | PolygonGeometry | MultiPolygonGeometry = Field(
        description="Feature geometry.",
    )
    properties: dict[str, Any] = Field(description="Feature properties.")


class FeatureNoMultiPolygon(StrictBaseModel):
    """GeoJSON feature that excludes multi-polygon geometry."""

    id: str = Field(min_length=1, description="Stable feature identifier.")
    type: Literal["Feature"] = Field(description="GeoJSON feature type.")
    geometry: PointGeometry | PolygonGeometry = Field(description="Feature geometry.")
    properties: dict[str, Any] = Field(description="Feature properties.")


class GeoJSON(StrictBaseModel):
    """GeoJSON FeatureCollection accepted by explicit location inputs."""

    type: Literal["FeatureCollection"] = Field(description="GeoJSON collection type.")
    features: list[Feature] = Field(
        min_length=1,
        description="One or more GeoJSON features.",
    )
    crs: CRS = Field(description="Coordinate reference system for all features.")


class SingleGeoJSON(StrictBaseModel):
    """GeoJSON FeatureCollection constrained to one non-multi-polygon feature."""

    type: Literal["FeatureCollection"] = Field(description="GeoJSON collection type.")
    features: list[FeatureNoMultiPolygon] = Field(
        min_length=1,
        max_length=1,
        description="Exactly one point or polygon feature.",
    )
    crs: CRS = Field(description="Coordinate reference system for the feature.")


__all__ = [
    "CRS",
    "CRSProperties",
    "Feature",
    "FeatureNoMultiPolygon",
    "GeoJSON",
    "MultiPolygonGeometry",
    "PointGeometry",
    "PolygonGeometry",
    "SingleGeoJSON",
]
