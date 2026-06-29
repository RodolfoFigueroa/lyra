from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

from lyra_app.models import ExplicitBoundsUnion, ExplicitLocationUnion

if TYPE_CHECKING:
    from lyra.sdk.models.plugin_v3 import SpatialInputKindV3

_LOCATION_WRAPPER_ADAPTER = TypeAdapter(ExplicitLocationUnion)
_BOUNDS_WRAPPER_ADAPTER = TypeAdapter(ExplicitBoundsUnion)


class SpatialInputValidationError(Exception):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__("spatial input validation failed")


class SpatialInputResolutionUnavailableError(Exception):
    pass


def _adapter_for_kind(kind: SpatialInputKindV3) -> TypeAdapter[Any]:
    return _LOCATION_WRAPPER_ADAPTER if kind == "location" else _BOUNDS_WRAPPER_ADAPTER


def _format_pydantic_errors(
    field_name: str,
    exc: PydanticValidationError,
) -> list[dict[str, Any]]:
    return [
        {
            "loc": [field_name, *list(error.get("loc", []))],
            "msg": str(error.get("msg", "Invalid spatial input.")),
            "type": str(error.get("type", "value_error")),
        }
        for error in exc.errors()
    ]


def resolve_spatial_inputs(
    payload: dict[str, Any],
    spatial_inputs: dict[str, SpatialInputKindV3],
) -> dict[str, Any]:
    from lyra_app.converters import converter_map  # noqa: PLC0415

    resolved = dict(payload)
    for field_name, kind in spatial_inputs.items():
        try:
            wrapper = _adapter_for_kind(kind).validate_python(payload[field_name])
        except PydanticValidationError as exc:
            raise SpatialInputValidationError(
                _format_pydantic_errors(field_name, exc)
            ) from exc

        data_type = str(wrapper.data_type)
        value = wrapper.value
        converter = converter_map[kind][data_type]

        try:
            geojson = converter(value)
        except PydanticValidationError as exc:
            raise SpatialInputValidationError(
                _format_pydantic_errors(field_name, exc)
            ) from exc
        except ValueError as exc:
            raise SpatialInputValidationError(
                [{"loc": [field_name], "msg": str(exc), "type": "value_error"}]
            ) from exc
        except (KeyError, SQLAlchemyError) as exc:
            msg = f"Failed to resolve spatial input {field_name!r}."
            raise SpatialInputResolutionUnavailableError(msg) from exc

        resolved[field_name] = geojson.model_dump(mode="json")

    return resolved
