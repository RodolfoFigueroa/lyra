from __future__ import annotations

from typing import Annotated, ClassVar, Literal

# Pydantic resolves these annotations while constructing the public wire models.
from lyra.sdk.models import geometry  # noqa: TC002
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import (
    AfterValidator,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)


def validate_cvegeos(value: list[str]) -> list[str]:
    """Validate a non-empty, same-level list of INEGI CVEGEO identifiers."""
    if not value:
        msg = "CVEGEO lists must contain at least one identifier."
        raise ValueError(msg)
    unique_lengths = {len(item) for item in value}
    if len(unique_lengths) > 1:
        msg = "All CVEGEO strings must have the same length."
        raise ValueError(msg)
    allowed_lengths = {2, 5, 9, 13, 16}
    found_length = next(iter(unique_lengths))
    if found_length not in allowed_lengths:
        msg = (
            f"CVEGEO strings must have length in {allowed_lengths}, but got "
            f"length {found_length}."
        )
        raise ValueError(msg)
    return value


class _SpatialReference(StrictBaseModel):
    @model_serializer(mode="wrap")
    def _serialize_with_discriminator(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, object]:
        value = handler(self)
        if not isinstance(value, dict):
            msg = "spatial reference serialization must produce an object"
            raise TypeError(msg)
        data_type = getattr(self, "data_type", None)
        if not isinstance(data_type, str):
            msg = "spatial reference is missing its discriminator"
            raise TypeError(msg)
        value["data_type"] = data_type
        return value


class CVEGEOList(_SpatialReference):
    """A list of same-level INEGI CVEGEO identifiers."""

    DATA_TYPE_DESCRIPTION: ClassVar[str] = (
        "A list of CVEGEOs. All CVEGEOs must have the same length, which "
        "determines their geographic level."
    )
    data_type: Literal["cvegeo_list"] = "cvegeo_list"
    value: Annotated[list[str], AfterValidator(validate_cvegeos)]


class GeoJSONLocation(_SpatialReference):
    """A GeoJSON feature collection accepted as a location."""

    DATA_TYPE_DESCRIPTION: ClassVar[str] = "A GeoDataFrame in GeoJSON format."
    data_type: Literal["geojson"] = "geojson"
    value: geometry.GeoJSON


class GeoJSONBounds(_SpatialReference):
    """A single GeoJSON geometry accepted as analysis bounds."""

    DATA_TYPE_DESCRIPTION: ClassVar[str] = (
        "A GeoDataFrame in GeoJSON format containing a single geometry. "
        "Does not support MultiPolygon or GeometryCollection."
    )
    data_type: Literal["geojson"] = "geojson"
    value: geometry.SingleGeoJSON


class MetZoneCode(_SpatialReference):
    """A metropolitan-zone code reference."""

    DATA_TYPE_DESCRIPTION: ClassVar[str] = "The code of a metropolitan zone."
    data_type: Literal["met_zone_code"] = "met_zone_code"
    value: str = Field(min_length=1)


LocationReference = Annotated[
    CVEGEOList | GeoJSONLocation | MetZoneCode,
    Field(discriminator="data_type"),
]
BoundsReference = Annotated[
    CVEGEOList | GeoJSONBounds | MetZoneCode,
    Field(discriminator="data_type"),
]


__all__ = [
    "BoundsReference",
    "CVEGEOList",
    "GeoJSONBounds",
    "GeoJSONLocation",
    "LocationReference",
    "MetZoneCode",
    "validate_cvegeos",
]
