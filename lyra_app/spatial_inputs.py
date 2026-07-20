from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lyra.sdk.models import RowIdentityMetadata
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

from lyra_app.models import ExplicitBoundsUnion, ExplicitLocationUnion

if TYPE_CHECKING:
    from lyra.sdk.models.plugin_v3 import SpatialInputKindV3

_LOCATION_WRAPPER_ADAPTER = TypeAdapter(ExplicitLocationUnion)
_BOUNDS_WRAPPER_ADAPTER = TypeAdapter(ExplicitBoundsUnion)

_CVEGEO_NAMESPACES_BY_LENGTH = {
    2: "inegi:cvegeo:state",
    5: "inegi:cvegeo:municipality",
    9: "inegi:cvegeo:locality",
    13: "inegi:cvegeo:ageb",
    16: "inegi:cvegeo:block",
}


@dataclass(frozen=True)
class SpatialInputResolution:
    """Resolved worker input plus identity metadata safe to retain."""

    input: dict[str, Any]
    row_identity: RowIdentityMetadata | None


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
    converter_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if converter_map is None:
        from lyra_app import converters  # noqa: PLC0415

        converter_map = vars(converters)["converter_map"]
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


def resolve_spatial_inputs_with_metadata(
    payload: dict[str, Any],
    spatial_inputs: dict[str, SpatialInputKindV3],
    converter_map: dict[str, dict[str, Any]] | None = None,
) -> SpatialInputResolution:
    """Resolve spatial inputs while retaining no resolved geometry in metadata."""

    resolved = resolve_spatial_inputs(payload, spatial_inputs, converter_map)
    row_identity: RowIdentityMetadata | None = None
    for field_name, kind in spatial_inputs.items():
        if kind != "location":
            continue

        wrapper = _LOCATION_WRAPPER_ADAPTER.validate_python(payload[field_name])
        if wrapper.data_type == "met_zone_code":
            row_identity = RowIdentityMetadata(
                field="cvegeo",
                namespace="inegi:cvegeo:ageb",
                version="2020",
            )
        elif wrapper.data_type == "cvegeo_list":
            namespace = _CVEGEO_NAMESPACES_BY_LENGTH.get(len(wrapper.value[0]))
            row_identity = RowIdentityMetadata(
                field="cvegeo",
                namespace=namespace,
                version="2020" if namespace is not None else None,
            )
        else:
            row_identity = RowIdentityMetadata(field="id")
        break

    return SpatialInputResolution(input=resolved, row_identity=row_identity)
