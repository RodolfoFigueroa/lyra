from __future__ import annotations

import inspect
import json
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    Literal,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from lyra.sdk.context import RunContext
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.plugin_v3 import (
    BatchInputV3,
    BooleanInputV3,
    BoundsInputV3,
    CompiledPluginManifestV3,
    EnumInputV3,
    InputSpecV3,
    IntegerInputV3,
    JsonSchemaInputV3,
    LocationInputV3,
    MetricManifestV3,
    NumberInputV3,
    OutputSpecV3,
    PluginInfoV3,
    PluginManifestV3,
    PluginOwnedInputSpecV3,
    StringInputV3,
    compile_plugin_manifest,
)
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

if TYPE_CHECKING:
    from lyra.sdk.models.job import JobEnvelope

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")

_BATCH_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_MISSING = inspect.Parameter.empty


@dataclass(frozen=True)
class _SpatialInputMarker:
    kind: Literal["location", "bounds"]


@dataclass(frozen=True)
class Batch:
    """Declare a metric-local batch input around ``list[BatchItem[T]]``."""

    max_items: int
    label: bool = False

    def __post_init__(self) -> None:
        if self.max_items < 1:
            msg = "Batch.max_items must be at least 1"
            raise ValueError(msg)


class BatchItem(StrictBaseModel, Generic[InputT]):
    """One parsed item supplied to a batch metric argument."""

    key: str = Field(min_length=1, max_length=64, pattern=_BATCH_KEY_PATTERN)
    value: InputT
    label: str | None = Field(default=None, min_length=1, max_length=120)


LocationInput = Annotated[GeoJSON, _SpatialInputMarker("location")]
BoundsInput = Annotated[SingleGeoJSON, _SpatialInputMarker("bounds")]


class PluginDefinitionError(ValueError):
    """Raised when a typed plugin definition cannot produce a valid contract."""


@dataclass(frozen=True)
class _MetricParameter:
    name: str
    adapter: TypeAdapter[Any]
    default: Any
    batch: Batch | None


@dataclass(frozen=True)
class MetricDefinition(Generic[ResultT]):
    """One registered metric and its resolved-input runtime adapter."""

    name: str
    description: str
    output: OutputSpecV3
    function: Callable[..., ResultT]
    inputs: dict[str, InputSpecV3]
    parameters: tuple[_MetricParameter, ...]
    accepts_context: bool

    def manifest_metric(self, entrypoint: str) -> MetricManifestV3:
        return MetricManifestV3(
            name=self.name,
            description=self.description,
            entrypoint=entrypoint,
            inputs=self.inputs,
            output=self.output,
        )

    def invoke(self, job: JobEnvelope, context: RunContext) -> ResultT:
        expected = {parameter.name for parameter in self.parameters}
        unexpected = sorted(set(job.input) - expected)
        if unexpected:
            names = ", ".join(unexpected)
            msg = f"Metric {self.name!r} received unexpected input field(s): {names}"
            raise PluginDefinitionError(msg)

        kwargs: dict[str, Any] = {}
        for parameter in self.parameters:
            if parameter.name not in job.input:
                if parameter.default is _MISSING:
                    msg = (
                        f"Metric {self.name!r} is missing required input "
                        f"{parameter.name!r}"
                    )
                    raise PluginDefinitionError(msg)
                continue

            raw_value = job.input[parameter.name]
            if parameter.batch is not None:
                _validate_batch_runtime_value(
                    self.name,
                    parameter.name,
                    raw_value,
                    parameter.batch,
                )
            try:
                kwargs[parameter.name] = parameter.adapter.validate_python(raw_value)
            except ValidationError as exc:
                msg = (
                    f"Metric {self.name!r} input {parameter.name!r} did not match "
                    f"its Python annotation: {exc}"
                )
                raise PluginDefinitionError(msg) from exc

        if self.accepts_context:
            kwargs["context"] = context
        return self.function(**kwargs)


class PluginDefinition:
    """Registry for typed metric functions and worker-side job dispatch."""

    def __init__(self) -> None:
        self._metrics: dict[str, MetricDefinition[Any]] = {}

    @property
    def metric_names(self) -> tuple[str, ...]:
        return tuple(self._metrics)

    def metric(
        self,
        *,
        name: str,
        description: str,
        output: OutputSpecV3,
    ) -> Callable[[Callable[..., ResultT]], Callable[..., ResultT]]:
        """Register a typed metric while returning its function unchanged."""

        def decorator(function: Callable[..., ResultT]) -> Callable[..., ResultT]:
            if name in self._metrics:
                msg = f"Duplicate metric name in PluginDefinition: {name!r}"
                raise PluginDefinitionError(msg)
            definition = _build_metric_definition(
                name=name,
                description=description,
                output=output,
                function=function,
            )
            definition.manifest_metric("lyra_plugin:plugin")
            self._metrics[name] = definition
            return function

        return decorator

    def manifest(
        self,
        *,
        plugin: PluginInfoV3,
        entrypoint: str,
    ) -> PluginManifestV3:
        if not self._metrics:
            msg = "PluginDefinition must register at least one metric"
            raise PluginDefinitionError(msg)
        return PluginManifestV3(
            schema_version=3,
            plugin=plugin,
            metrics=[
                definition.manifest_metric(entrypoint)
                for definition in self._metrics.values()
            ],
        )

    def compiled_manifest(
        self,
        *,
        plugin: PluginInfoV3,
        entrypoint: str,
    ) -> CompiledPluginManifestV3:
        return compile_plugin_manifest(
            self.manifest(plugin=plugin, entrypoint=entrypoint)
        )

    def __call__(self, job: JobEnvelope, context: RunContext) -> Any:
        try:
            metric = self._metrics[job.metric]
        except KeyError as exc:
            msg = f"PluginDefinition does not register metric {job.metric!r}"
            raise PluginDefinitionError(msg) from exc
        return metric.invoke(job, context)


def _unwrap_annotated(annotation: Any) -> tuple[Any, list[Any]]:
    metadata: list[Any] = []
    value = annotation
    while get_origin(value) is Annotated:
        args = get_args(value)
        value = args[0]
        metadata.extend(args[1:])
    return value, metadata


def _split_nullable(annotation: Any) -> tuple[Any, bool]:
    base, metadata = _unwrap_annotated(annotation)
    origin = get_origin(base)
    if origin not in {Union, types.UnionType}:
        return annotation, False
    args = get_args(base)
    non_none = tuple(value for value in args if value is not type(None))
    if len(non_none) == len(args):
        return annotation, False
    if len(non_none) != 1:
        return annotation, False
    value: Any = non_none[0]
    if metadata:
        value = Annotated[value, *metadata]
    return value, True


def _schema_metadata(annotation: Any, schema: dict[str, Any]) -> dict[str, Any]:
    _base, annotation_metadata = _unwrap_annotated(annotation)
    if not any(isinstance(value, FieldInfo) for value in annotation_metadata):
        return {}
    metadata: dict[str, Any] = {}
    if isinstance(schema.get("description"), str):
        metadata["description"] = schema["description"]
    if isinstance(schema.get("examples"), list):
        metadata["examples"] = schema["examples"]
    return metadata


def _normal_input_spec(
    annotation: Any,
    *,
    default: Any,
) -> PluginOwnedInputSpecV3:
    annotation, nullable = _split_nullable(annotation)
    schema = TypeAdapter(annotation).json_schema()
    if not schema:
        msg = "metric input annotations must describe a JSON-compatible type, not Any"
        raise PluginDefinitionError(msg)

    common = _schema_metadata(annotation, schema)
    if default is not _MISSING:
        common["required"] = False
        common["default"] = default
    if nullable:
        common["nullable"] = True

    schema_without_metadata = {
        key: value
        for key, value in schema.items()
        if key not in {"description", "examples", "title", "default"}
    }
    schema_type = schema_without_metadata.get("type")
    keys = set(schema_without_metadata)

    if schema_type == "string" and keys <= {
        "type",
        "minLength",
        "maxLength",
        "pattern",
    }:
        constraints = {
            field: schema_without_metadata[key]
            for field, key in (
                ("min_length", "minLength"),
                ("max_length", "maxLength"),
                ("pattern", "pattern"),
            )
            if key in schema_without_metadata
        }
        return StringInputV3(kind="string", **constraints, **common)
    if schema_type == "number" and keys <= {"type", "minimum", "maximum"}:
        constraints = {
            key: schema_without_metadata[key]
            for key in ("minimum", "maximum")
            if key in schema_without_metadata
        }
        return NumberInputV3(kind="number", **constraints, **common)
    if schema_type == "integer" and keys <= {"type", "minimum", "maximum"}:
        constraints = {
            key: schema_without_metadata[key]
            for key in ("minimum", "maximum")
            if key in schema_without_metadata
        }
        return IntegerInputV3(kind="integer", **constraints, **common)
    if schema_type == "boolean" and keys == {"type"}:
        return BooleanInputV3(kind="boolean", **common)
    if isinstance(schema_without_metadata.get("enum"), list) and keys <= {
        "enum",
        "type",
    }:
        return EnumInputV3(
            kind="enum",
            values=schema_without_metadata["enum"],
            **common,
        )
    return JsonSchemaInputV3(
        kind="json_schema",
        schema=schema_without_metadata,
        **common,
    )


def _spatial_marker(annotation: Any) -> _SpatialInputMarker | None:
    _base, metadata = _unwrap_annotated(annotation)
    markers = [value for value in metadata if isinstance(value, _SpatialInputMarker)]
    if len(markers) > 1:
        msg = "metric inputs may contain only one spatial marker"
        raise PluginDefinitionError(msg)
    return markers[0] if markers else None


def _batch_marker(annotation: Any) -> Batch | None:
    _base, metadata = _unwrap_annotated(annotation)
    markers = [value for value in metadata if isinstance(value, Batch)]
    if len(markers) > 1:
        msg = "metric inputs may contain only one Batch marker"
        raise PluginDefinitionError(msg)
    return markers[0] if markers else None


def _batch_value_annotation(annotation: Any) -> Any:
    base, _metadata = _unwrap_annotated(annotation)
    if get_origin(base) is not list:
        msg = "Batch inputs must annotate list[BatchItem[T]]"
        raise PluginDefinitionError(msg)
    item_type = get_args(base)[0]
    generic_metadata = getattr(item_type, "__pydantic_generic_metadata__", None)
    if not isinstance(generic_metadata, dict):
        msg = "Batch inputs must annotate list[BatchItem[T]]"
        raise PluginDefinitionError(msg)
    origin = generic_metadata.get("origin")
    args = generic_metadata.get("args")
    if origin is not BatchItem or not isinstance(args, tuple) or len(args) != 1:
        msg = "Batch inputs must annotate list[BatchItem[T]]"
        raise PluginDefinitionError(msg)
    return args[0]


def _input_spec(
    annotation: Any,
    *,
    default: Any,
) -> tuple[InputSpecV3, Batch | None]:
    spatial = _spatial_marker(annotation)
    batch = _batch_marker(annotation)
    if spatial is not None and batch is not None:
        msg = "metric inputs cannot be both spatial and batch inputs"
        raise PluginDefinitionError(msg)

    schema = TypeAdapter(annotation).json_schema()
    common = _schema_metadata(annotation, schema)
    if spatial is not None:
        if default is not _MISSING:
            msg = "spatial metric inputs cannot define defaults"
            raise PluginDefinitionError(msg)
        if spatial.kind == "location":
            return LocationInputV3(kind="location", **common), None
        return BoundsInputV3(kind="bounds", **common), None

    if batch is not None:
        if default is not _MISSING:
            msg = "batch metric inputs cannot define defaults"
            raise PluginDefinitionError(msg)
        value_annotation = _batch_value_annotation(annotation)
        value_spec = _normal_input_spec(value_annotation, default=_MISSING)
        return (
            BatchInputV3(
                kind="batch",
                max_items=batch.max_items,
                value=value_spec,
                label=batch.label,
                **common,
            ),
            batch,
        )
    return _normal_input_spec(annotation, default=default), None


def _build_metric_definition(
    *,
    name: str,
    description: str,
    output: OutputSpecV3,
    function: Callable[..., ResultT],
) -> MetricDefinition[ResultT]:
    signature = inspect.signature(function)
    try:
        hints = get_type_hints(function, include_extras=True)
    except (NameError, TypeError) as exc:
        msg = f"Could not resolve annotations for metric {name!r}: {exc}"
        raise PluginDefinitionError(msg) from exc

    inputs: dict[str, InputSpecV3] = {}
    parameters: list[_MetricParameter] = []
    accepts_context = False
    for parameter in signature.parameters.values():
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            msg = (
                f"Metric {name!r} parameter {parameter.name!r} must be "
                "positional-or-keyword or keyword-only"
            )
            raise PluginDefinitionError(msg)
        annotation = hints.get(parameter.name, _MISSING)
        if parameter.name == "context":
            if parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
                msg = f"Metric {name!r} context must be keyword-only"
                raise PluginDefinitionError(msg)
            if annotation is not RunContext:
                msg = f"Metric {name!r} context must be annotated as RunContext"
                raise PluginDefinitionError(msg)
            accepts_context = True
            continue
        if annotation is _MISSING:
            msg = f"Metric {name!r} input {parameter.name!r} must have an annotation"
            raise PluginDefinitionError(msg)
        try:
            input_spec, batch = _input_spec(
                annotation,
                default=parameter.default,
            )
            adapter = TypeAdapter(annotation)
        except (TypeError, ValueError) as exc:
            if isinstance(exc, PluginDefinitionError):
                raise
            msg = f"Could not compile metric {name!r} input {parameter.name!r}: {exc}"
            raise PluginDefinitionError(msg) from exc
        inputs[parameter.name] = input_spec
        parameters.append(
            _MetricParameter(
                name=parameter.name,
                adapter=adapter,
                default=parameter.default,
                batch=batch,
            )
        )

    return MetricDefinition(
        name=name,
        description=description,
        output=output,
        function=function,
        inputs=inputs,
        parameters=tuple(parameters),
        accepts_context=accepts_context,
    )


def _validate_batch_runtime_value(
    metric_name: str,
    field_name: str,
    value: Any,
    batch: Batch,
) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= batch.max_items:
        msg = (
            f"Metric {metric_name!r} batch input {field_name!r} must contain "
            f"between 1 and {batch.max_items} item(s)"
        )
        raise PluginDefinitionError(msg)
    seen_keys: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            msg = (
                f"Metric {metric_name!r} batch input {field_name!r} "
                "must contain objects"
            )
            raise PluginDefinitionError(msg)
        if not batch.label and "label" in item:
            msg = (
                f"Metric {metric_name!r} batch input {field_name!r} does not "
                "accept labels"
            )
            raise PluginDefinitionError(msg)
        key = item.get("key")
        if isinstance(key, str):
            if key in seen_keys:
                msg = (
                    f"Metric {metric_name!r} batch input {field_name!r} contains "
                    f"duplicate key {key!r}"
                )
                raise PluginDefinitionError(msg)
            seen_keys.add(key)
    identities = [json.dumps(item, sort_keys=True) for item in value]
    if len(identities) != len(set(identities)):
        msg = f"Metric {metric_name!r} batch input {field_name!r} contains duplicates"
        raise PluginDefinitionError(msg)


__all__ = [
    "Batch",
    "BatchItem",
    "BoundsInput",
    "LocationInput",
    "MetricDefinition",
    "PluginDefinition",
    "PluginDefinitionError",
]
