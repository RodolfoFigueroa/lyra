from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

from lyra_app.models import ExplicitBoundsUnion, ExplicitLocationUnion

if TYPE_CHECKING:
    from lyra.sdk.models.plugin_v2 import MetricManifestV2, SpatialInputKind

_LOCATION_WRAPPER_ADAPTER = TypeAdapter(ExplicitLocationUnion)
_BOUNDS_WRAPPER_ADAPTER = TypeAdapter(ExplicitBoundsUnion)


class SpatialInputValidationError(Exception):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__("spatial input validation failed")


class SpatialInputResolutionUnavailableError(Exception):
    pass


def _adapter_for_kind(kind: SpatialInputKind) -> TypeAdapter[Any]:
    return _LOCATION_WRAPPER_ADAPTER if kind == "location" else _BOUNDS_WRAPPER_ADAPTER


def _const_to_enum(value: Any) -> Any:
    if isinstance(value, dict):
        converted = {
            key: _const_to_enum(item) for key, item in value.items() if key != "const"
        }
        if "const" in value:
            converted["enum"] = [deepcopy(value["const"])]
        return converted
    if isinstance(value, list):
        return [_const_to_enum(item) for item in value]
    return deepcopy(value)


def _wrapper_field_schema(
    kind: SpatialInputKind,
) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = _const_to_enum(_adapter_for_kind(kind).json_schema())
    defs = schema.pop("$defs", {})
    if not isinstance(defs, dict):
        msg = f"Spatial wrapper schema for {kind!r} did not contain object $defs."
        raise TypeError(msg)
    return schema, defs


def contains_feature_collection_schema(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("const") == "FeatureCollection":
            return True
        enum = value.get("enum")
        if isinstance(enum, list) and "FeatureCollection" in enum:
            return True
        return any(contains_feature_collection_schema(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_feature_collection_schema(item) for item in value)
    return False


def build_effective_request_schema(metric: MetricManifestV2) -> dict[str, Any]:
    if contains_feature_collection_schema(metric.request_schema):
        msg = (
            f"Metric {metric.name!r} declares a raw GeoJSON/FeatureCollection "
            "request schema. Spatial inputs must use wrapper fields."
        )
        raise RuntimeError(msg)

    schema = deepcopy(metric.request_schema)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        msg = f"Metric {metric.name!r} request_schema must define object properties."
        raise TypeError(msg)

    root_defs = schema.setdefault("$defs", {})
    if not isinstance(root_defs, dict):
        msg = f"Metric {metric.name!r} request_schema $defs must be an object."
        raise TypeError(msg)

    for field_name, kind in metric.spatial_inputs.items():
        field_schema, field_defs = _wrapper_field_schema(kind)
        properties[field_name] = field_schema
        for def_name, definition in field_defs.items():
            existing_definition = root_defs.get(def_name)
            if existing_definition is not None and existing_definition != definition:
                msg = (
                    f"Metric {metric.name!r} request_schema $defs conflicts with "
                    f"canonical spatial definition {def_name!r}."
                )
                raise RuntimeError(msg)
            root_defs[def_name] = definition

    return schema


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
    spatial_inputs: dict[str, SpatialInputKind],
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
