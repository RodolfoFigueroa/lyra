from __future__ import annotations

from typing import Annotated, TypeAlias

from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from pydantic import TypeAdapter
from typing_extensions import TypeAliasType

REQUIRE_EXPLICIT_TYPE = "REQUIRE_EXPLICIT_TYPE"
REQUIRE_EXPLICIT_BOUNDS_TYPE = "REQUIRE_EXPLICIT_BOUNDS_TYPE"

ExplicitLocationAPI = Annotated[GeoJSON, REQUIRE_EXPLICIT_TYPE]
ExplicitBoundsAPI = Annotated[SingleGeoJSON, REQUIRE_EXPLICIT_BOUNDS_TYPE]

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue = TypeAliasType(
    "JsonValue",
    JsonScalar | list["JsonValue"] | dict[str, "JsonValue"],
)
JsonObject: TypeAlias = dict[str, JsonValue]

_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_JSON_OBJECT_ADAPTER: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def validate_json_value(value: object) -> JsonValue:
    """Validate and normalize an arbitrary boundary value as recursive JSON."""

    return _JSON_VALUE_ADAPTER.validate_python(value)


def validate_json_object(value: object) -> JsonObject:
    """Validate and normalize an arbitrary boundary value as a JSON object."""

    return _JSON_OBJECT_ADAPTER.validate_python(value)


__all__ = [
    "REQUIRE_EXPLICIT_BOUNDS_TYPE",
    "REQUIRE_EXPLICIT_TYPE",
    "ExplicitBoundsAPI",
    "ExplicitLocationAPI",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "validate_json_object",
    "validate_json_value",
]
