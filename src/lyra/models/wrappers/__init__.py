from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import UnionType
from typing import Annotated, Literal, get_args, get_origin

from pydantic import Field
from typing_extensions import TypedDict

from lyra.models.base import GeoJSON, StrictBaseModel
from lyra.models.wrappers.cvegeo_list import CVEGEOListWrapper
from lyra.models.wrappers.geojson import GeoJSONWrapper, SingleGeoJSONWrapper
from lyra.models.wrappers.met_zone_code import MetZoneCodeWrapper


class WrapperDataTypeInfo(TypedDict):
    data_type: str
    description: str


def _extract_data_type(model_class: type[StrictBaseModel]) -> str:
    field_info = model_class.model_fields.get("data_type")
    if field_info is None:
        err = f"Missing 'data_type' field in wrapper {model_class.__name__}."
        raise RuntimeError(err)

    annotation = field_info.annotation
    if get_origin(annotation) is Literal:
        args = get_args(annotation)
        if len(args) == 1 and isinstance(args[0], str):
            return args[0]

    err = (
        f"Wrapper {model_class.__name__} must declare data_type as a single "
        "Literal string value."
    )
    raise RuntimeError(err)


def _extract_data_type_description(model_class: type[StrictBaseModel]) -> str:
    description = getattr(model_class, "DATA_TYPE_DESCRIPTION", None)
    if isinstance(description, str) and description.strip():
        return description.strip()

    err = (
        f"Wrapper {model_class.__name__} must define a non-empty "
        "DATA_TYPE_DESCRIPTION class variable."
    )
    raise RuntimeError(err)


def get_wrapper_data_type_info() -> list[WrapperDataTypeInfo]:
    return [
        {
            "data_type": _extract_data_type(wrapper_class),
            "description": _extract_data_type_description(wrapper_class),
        }
        for wrapper_class in [
            CVEGEOListWrapper,
            GeoJSONWrapper,
            SingleGeoJSONWrapper,
            MetZoneCodeWrapper,
        ]
    ]


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
