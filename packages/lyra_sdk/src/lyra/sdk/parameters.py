"""Supported parameter-model inspection and complete request schema generation."""

from __future__ import annotations

import types
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    NoReturn,
    Union,
    get_args,
    get_origin,
)

from jsonschema.exceptions import ValidationError as SchemaValidationError
from lyra.sdk.client_contract import JSON_SCHEMA_DIALECT
from lyra.sdk.errors import PluginDefinitionError
from lyra.sdk.models.spatial import BoundsReference, LocationReference
from lyra.sdk.schema import (
    check_json_value,
    check_request_schema,
    schema_nodes,
    subschema_validator,
)
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

if TYPE_CHECKING:
    from lyra.sdk.types import JsonObject

_SCHEMA_ANNOTATIONS = {"title", "description", "examples", "deprecated"}


class MetricParameters(BaseModel):
    """Convenient parameter base with explicit fields and validated defaults."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


def _fail(metric: str, path: str, detail: str) -> NoReturn:
    msg = f"Metric {metric!r}, {path}: {detail}"
    raise PluginDefinitionError(msg)


def _check_json(value: object, metric: str, path: str) -> None:
    try:
        check_json_value(value, path)
    except (TypeError, ValueError) as exc:
        msg = f"Metric {metric!r}, {path}: must be a finite JSON-serializable value"
        raise PluginDefinitionError(msg) from exc


def _check_schema_extra(extra: object, metric: str, path: str) -> None:
    if extra is None:
        return
    if not isinstance(extra, dict) or set(extra) - _SCHEMA_ANNOTATIONS:
        _fail(metric, path, "structural JSON Schema overrides are unsupported")
    _check_json(extra, metric, path)


def _check_metadata(metadata: object, metric: str, path: str) -> None:
    if isinstance(metadata, FieldInfo):
        _check_field_options(metadata, metric, path)
    elif hasattr(metadata, "__get_pydantic_json_schema__") or type(
        metadata
    ).__name__ in {"PlainSerializer", "WrapSerializer"}:
        _fail(metric, path, "custom schema hooks and serializers are unsupported")
    elif (
        getattr(metadata, "json_schema_input_type", PydanticUndefined)
        is not PydanticUndefined
    ):
        _fail(metric, path, "validator input schema overrides are unsupported")


def _check_field_options(field: FieldInfo, metric: str, path: str) -> None:
    if any(
        value is not None
        for value in (
            field.alias,
            field.validation_alias,
            field.serialization_alias,
        )
    ):
        _fail(metric, path, "field aliases are unsupported")
    if field.default_factory is not None:
        _fail(metric, path, "default factories are unsupported")
    if field.validate_default is False:
        _fail(metric, path, "defaults must be validated")
    _check_schema_extra(field.json_schema_extra, metric, path)
    if not field.is_required():
        _check_json(field.default, metric, f"{path}.default")
    if field.examples is not None:
        _check_json(field.examples, metric, f"{path}.examples")


def _check_model_options(model: type[BaseModel], metric: str, path: str) -> None:
    if model.model_config.get("extra") != "forbid":
        _fail(metric, path, 'parameter models require extra="forbid"')
    if model.model_config.get("validate_default") is not True:
        _fail(metric, path, "parameter models require validate_default=True")
    if (
        model.__pydantic_root_model__
        or model.__pydantic_generic_metadata__["parameters"]
    ):
        _fail(metric, path, "root and unresolved generic models are unsupported")
    if not model.__pydantic_complete__:
        _fail(metric, path, "model annotations must be fully resolved")
    decorators = model.__pydantic_decorators__
    if (
        decorators.field_serializers
        or decorators.model_serializers
        or model.model_computed_fields
    ):
        _fail(metric, path, "custom serializers and computed fields are unsupported")
    _check_model_customization(model, metric, path)


def _check_model_customization(model: type[BaseModel], metric: str, path: str) -> None:
    for base in model.__mro__:
        if base is BaseModel:
            break
        if "__get_pydantic_json_schema__" in vars(
            base
        ) or "__get_pydantic_core_schema__" in vars(base):
            _fail(metric, path, "custom schema hooks are unsupported")
    _check_schema_extra(model.model_config.get("json_schema_extra"), metric, path)
    for validator in model.__pydantic_decorators__.field_validators.values():
        if validator.info.json_schema_input_type is not PydanticUndefined:
            _fail(metric, path, "validator input schema overrides are unsupported")


def _check_literal(values: tuple[object, ...], metric: str, path: str) -> None:
    if not values or len({type(value) for value in values}) != 1:
        _fail(metric, path, "Literal values must be homogeneous JSON scalars")
    if any(type(value) not in {str, bool, int, float, type(None)} for value in values):
        _fail(metric, path, "Literal values must be JSON scalars")
    for value in values:
        _check_json(value, metric, path)


def _check_annotation(
    annotation: object,
    metric: str,
    path: str,
    active: set[type[BaseModel]],
) -> None:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        for metadata in args[1:]:
            _check_metadata(metadata, metric, path)
        _check_annotation(args[0], metric, path, active)
    elif annotation in {str, bool, int, float, type(None)}:
        return
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        _check_model(annotation, metric, path, active)
    elif origin is Literal:
        _check_literal(args, metric, path)
    elif origin in {Union, types.UnionType}:
        for member in args:
            _check_annotation(member, metric, path, active)
    elif origin is list and len(args) == 1:
        _check_annotation(args[0], metric, f"{path}[]", active)
    elif origin is dict and len(args) == 2 and args[0] is str:
        _check_annotation(args[1], metric, f"{path}.*", active)
    else:
        _fail(metric, path, f"unsupported parameter annotation {annotation!r}")


def _check_model(
    model: type[BaseModel],
    metric: str,
    path: str,
    active: set[type[BaseModel]],
) -> None:
    if model in active:
        _fail(metric, path, "recursive parameter models are unsupported")
    _check_model_options(model, metric, path)
    active.add(model)
    try:
        for name, field in model.model_fields.items():
            field_path = f"{path}.{name}"
            if path == "parameters" and not (field.description or "").strip():
                _fail(metric, field_path, "root parameter fields require descriptions")
            _check_field_options(field, metric, field_path)
            for metadata in field.metadata:
                _check_metadata(metadata, metric, field_path)
            _check_annotation(field.annotation, metric, field_path, active)
    finally:
        active.remove(model)


def validate_parameter_model(model: type[BaseModel], metric: str) -> None:
    """Reject unsupported authoring capabilities without changing the model."""
    _check_model(model, metric, "parameters", set())


def _check_schema_values(schema: JsonObject, metric: str) -> None:
    for path, node in schema_nodes(schema):
        values: list[tuple[str, object]] = []
        if "default" in node:
            values.append(("default", node["default"]))
        values.extend(
            (f"examples[{index}]", value)
            for index, value in enumerate(node.get("examples", []))
        )
        validator = subschema_validator(schema, node)
        for label, value in values:
            _check_json(value, metric, f"{path}/{label}")
            try:
                validator.validate(value)
            except SchemaValidationError as exc:
                msg = f"Metric {metric!r}, {path}/{label}: {exc.message}"
                raise PluginDefinitionError(msg) from exc


def build_request_schema(
    metric: str,
    parameters: type[BaseModel] | None,
    spatial: set[str],
) -> JsonObject:
    """Generate one complete request schema with Pydantic-owned local definitions.

    Returns:
        The validated Draft 2020-12 public request schema.

    Raises:
        PluginDefinitionError: If the generated contract or defaults are invalid.
    """
    fields: dict[str, Any] = {}
    if parameters is not None:
        validate_parameter_model(parameters, metric)
        fields["parameters"] = (parameters, ...)
    for name in sorted(spatial):
        reference = LocationReference if name == "location" else BoundsReference
        fields[name] = (
            reference,
            Field(description=f"Lyra-resolved {name} reference."),
        )
    try:
        root = create_model(
            f"{metric}_request", __config__=ConfigDict(extra="forbid"), **fields
        )
        schema = root.model_json_schema(mode="validation")
        schema["$schema"] = JSON_SCHEMA_DIALECT
        check_request_schema(schema)
        _check_schema_values(schema, metric)
    except (TypeError, ValueError, KeyError) as exc:
        msg = f"Metric {metric!r}, request schema: {exc}"
        raise PluginDefinitionError(msg) from exc
    return schema
