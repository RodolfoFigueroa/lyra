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
    NotRequired,
    TypeAlias,
    TypedDict,
    TypeVar,
    Union,
    cast,
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
from lyra.sdk.types import JsonObject, JsonValue
from pydantic import Field, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from typing_extensions import TypeForm

if TYPE_CHECKING:
    from lyra.sdk.models.job import JobEnvelope

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")
PythonAnnotation: TypeAlias = TypeForm[Any] | str
PluginResult: TypeAlias = JsonValue | StrictBaseModel
ConstraintValue: TypeAlias = int | float


class CommonInputMetadata(TypedDict):
    description: NotRequired[str]
    examples: NotRequired[list[JsonValue]]
    required: NotRequired[bool]
    default: NotRequired[JsonValue]
    nullable: NotRequired[bool]


_BATCH_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_MISSING = inspect.Parameter.empty


@dataclass(frozen=True)
class _SpatialInputMarker:
    kind: Literal["location", "bounds"]


@dataclass(frozen=True, kw_only=True)
class Input:
    """Describe and constrain one plugin-owned metric input."""

    description: str
    examples: list[JsonValue] | None = None
    gt: ConstraintValue | None = None
    ge: ConstraintValue | None = None
    lt: ConstraintValue | None = None
    le: ConstraintValue | None = None
    multiple_of: ConstraintValue | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    strict: bool | None = None
    json_schema_extra: JsonObject | None = None

    def __post_init__(self) -> None:
        if not self.description.strip():
            msg = "Input.description must be a non-empty string"
            raise ValueError(msg)
        if self.examples is not None and not self.examples:
            msg = "Input.examples must contain at least one example when provided"
            raise ValueError(msg)


@dataclass(frozen=True, kw_only=True)
class BatchInput:
    """Describe a bounded ``list[BatchItem[T]]`` metric input."""

    max_items: int
    items: Input
    allow_labels: bool = False

    def __post_init__(self) -> None:
        if self.max_items < 1:
            msg = "BatchInput.max_items must be at least 1"
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


class MetricDescription(StrictBaseModel):
    """Structured author-facing description of one registered metric."""

    name: str
    description: str
    handler: str
    signature: str
    inputs: dict[str, InputSpecV3]
    output: OutputSpecV3


@dataclass(frozen=True)
class _MetricParameter:
    name: str
    adapter: TypeAdapter[Any]
    default: Any
    batch: BatchInput | None


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

    def describe(self) -> MetricDescription:
        module = getattr(self.function, "__module__", type(self.function).__module__)
        qualname = getattr(
            self.function,
            "__qualname__",
            type(self.function).__qualname__,
        )
        return MetricDescription(
            name=self.name,
            description=self.description,
            handler=f"{module}.{qualname}",
            signature=_format_function_signature(self.function),
            inputs=self.inputs,
            output=self.output,
        )

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
        inputs: Mapping[str, Input | BatchInput] | None = None,
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
                input_declarations=dict(inputs or {}),
            )
            definition.manifest_metric("lyra_plugin:plugin")
            self._metrics[name] = definition
            return function

        return decorator

    def describe(self, name: str) -> MetricDescription:
        """Return structured authoring information for one registered metric."""

        try:
            metric = self._metrics[name]
        except KeyError as exc:
            available = ", ".join(self.metric_names) or "none"
            msg = f"Unknown metric {name!r}; available metrics: {available}"
            raise PluginDefinitionError(msg) from exc
        return metric.describe()

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

    def __call__(self, job: JobEnvelope, context: RunContext) -> PluginResult:
        try:
            metric = self._metrics[job.metric]
        except KeyError as exc:
            msg = f"PluginDefinition does not register metric {job.metric!r}"
            raise PluginDefinitionError(msg) from exc
        return metric.invoke(job, context)


def _unwrap_annotated(
    annotation: PythonAnnotation,
) -> tuple[PythonAnnotation, list[Any]]:
    metadata: list[Any] = []
    value = annotation
    while get_origin(value) is Annotated:
        args = get_args(value)
        value = args[0]
        metadata.extend(args[1:])
    return value, metadata


def _with_annotation_metadata(
    annotation: PythonAnnotation,
    *metadata: FieldInfo | _SpatialInputMarker | JsonValue,
) -> TypeForm[Any]:
    if isinstance(annotation, str):
        msg = "Deferred string annotations cannot be decorated at runtime"
        raise PluginDefinitionError(msg)
    return Annotated[annotation, *metadata]


def _split_nullable(annotation: PythonAnnotation) -> tuple[PythonAnnotation, bool]:
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
    value: PythonAnnotation = non_none[0]
    if metadata:
        value = _with_annotation_metadata(value, *metadata)
    return value, True


def _schema_metadata(
    annotation: PythonAnnotation,
    schema: JsonObject,
) -> CommonInputMetadata:
    _base, annotation_metadata = _unwrap_annotated(annotation)
    if not any(isinstance(value, FieldInfo) for value in annotation_metadata):
        return {}
    metadata: CommonInputMetadata = {}
    description = schema.get("description")
    if isinstance(description, str):
        metadata["description"] = description
    examples = schema.get("examples")
    if isinstance(examples, list):
        metadata["examples"] = examples
    return metadata


def _normal_input_spec(
    annotation: PythonAnnotation,
    *,
    default: JsonValue | type,
) -> PluginOwnedInputSpecV3:
    annotation, nullable = _split_nullable(annotation)
    schema = TypeAdapter(annotation).json_schema()
    if not schema:
        msg = "metric input annotations must describe a JSON-compatible type, not Any"
        raise PluginDefinitionError(msg)

    common = _schema_metadata(annotation, schema)
    if default is not _MISSING:
        if isinstance(default, type):
            msg = "metric input defaults must be JSON-compatible values"
            raise PluginDefinitionError(msg)
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
        return StringInputV3.model_validate({"kind": "string", **constraints, **common})
    if schema_type == "number" and keys <= {"type", "minimum", "maximum"}:
        constraints = {
            key: schema_without_metadata[key]
            for key in ("minimum", "maximum")
            if key in schema_without_metadata
        }
        return NumberInputV3.model_validate({"kind": "number", **constraints, **common})
    if schema_type == "integer" and keys <= {"type", "minimum", "maximum"}:
        constraints = {
            key: schema_without_metadata[key]
            for key in ("minimum", "maximum")
            if key in schema_without_metadata
        }
        return IntegerInputV3.model_validate(
            {"kind": "integer", **constraints, **common}
        )
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


def _spatial_marker(annotation: PythonAnnotation) -> _SpatialInputMarker | None:
    _base, metadata = _unwrap_annotated(annotation)
    markers = [value for value in metadata if isinstance(value, _SpatialInputMarker)]
    if len(markers) > 1:
        msg = "metric inputs may contain only one spatial marker"
        raise PluginDefinitionError(msg)
    return markers[0] if markers else None


def _batch_value_annotation(annotation: PythonAnnotation) -> PythonAnnotation:
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


def _input_field(input_: Input) -> FieldInfo:
    return Field(
        description=input_.description,
        examples=input_.examples,
        json_schema_extra=input_.json_schema_extra,
        gt=input_.gt,
        ge=input_.ge,
        lt=input_.lt,
        le=input_.le,
        multiple_of=input_.multiple_of,
        min_length=input_.min_length,
        max_length=input_.max_length,
        pattern=input_.pattern,
        strict=input_.strict,
    )


def _reject_field_metadata(annotation: PythonAnnotation, *, location: str) -> None:
    _base, metadata = _unwrap_annotated(annotation)
    if any(isinstance(value, FieldInfo) for value in metadata):
        msg = (
            f"{location} contains Field metadata; move descriptions, examples, "
            "and constraints to the @plugin.metric inputs mapping"
        )
        raise PluginDefinitionError(msg)


def _input_spec(
    annotation: PythonAnnotation,
    *,
    declaration: Input | BatchInput | None,
    default: JsonValue | type,
) -> tuple[InputSpecV3, BatchInput | None, PythonAnnotation]:
    _reject_field_metadata(annotation, location="metric input annotation")
    spatial = _spatial_marker(annotation)

    if spatial is not None:
        if declaration is not None:
            msg = "spatial input metadata is owned by Lyra; remove its declaration"
            raise PluginDefinitionError(msg)
        if default is not _MISSING:
            msg = "spatial metric inputs cannot define defaults"
            raise PluginDefinitionError(msg)
        if spatial.kind == "location":
            return LocationInputV3(kind="location"), None, annotation
        return BoundsInputV3(kind="bounds"), None, annotation

    if isinstance(declaration, BatchInput):
        if default is not _MISSING:
            msg = "batch metric inputs cannot define defaults"
            raise PluginDefinitionError(msg)
        value_annotation = _batch_value_annotation(annotation)
        _reject_field_metadata(
            value_annotation,
            location="BatchItem value annotation",
        )
        effective_value_annotation = _with_annotation_metadata(
            value_annotation,
            _input_field(declaration.items),
        )
        batch_item_annotation = BatchItem.__class_getitem__(
            cast("type[Any]", effective_value_annotation)
        )
        effective_annotation = types.GenericAlias(list, batch_item_annotation)
        value_spec = _normal_input_spec(
            effective_value_annotation,
            default=_MISSING,
        )
        return (
            BatchInputV3(
                kind="batch",
                max_items=declaration.max_items,
                value=value_spec,
                label=declaration.allow_labels,
            ),
            declaration,
            effective_annotation,
        )
    if declaration is None:
        msg = "plugin-owned metric inputs must define an Input declaration"
        raise PluginDefinitionError(msg)
    try:
        _batch_value_annotation(annotation)
    except PluginDefinitionError:
        pass
    else:
        msg = "list[BatchItem[T]] inputs must use BatchInput, not Input"
        raise PluginDefinitionError(msg)
    effective_annotation = _with_annotation_metadata(
        annotation,
        _input_field(declaration),
    )
    return (
        _normal_input_spec(effective_annotation, default=default),
        None,
        effective_annotation,
    )


def _build_metric_definition(
    *,
    name: str,
    description: str,
    output: OutputSpecV3,
    function: Callable[..., ResultT],
    input_declarations: dict[str, Input | BatchInput],
) -> MetricDefinition[ResultT]:
    signature = inspect.signature(function)
    try:
        hints = get_type_hints(function, include_extras=True)
    except (NameError, TypeError) as exc:
        msg = f"Could not resolve annotations for metric {name!r}: {exc}"
        raise PluginDefinitionError(msg) from exc

    resolved_parameters = _resolve_metric_parameters(
        name=name,
        signature=signature,
        hints=hints,
    )
    _validate_input_declaration_names(
        name=name,
        function=function,
        resolved_parameters=resolved_parameters,
        input_declarations=input_declarations,
    )

    inputs: dict[str, InputSpecV3] = {}
    parameters: list[_MetricParameter] = []
    accepts_context = False
    for parameter, annotation in resolved_parameters:
        if parameter.name == "context":
            accepts_context = True
            continue
        declaration = input_declarations.get(parameter.name)
        try:
            input_spec, batch, effective_annotation = _input_spec(
                annotation,
                declaration=declaration,
                default=parameter.default,
            )
            adapter = TypeAdapter(effective_annotation)
        except (TypeError, ValueError) as exc:
            msg = (
                f"Metric {name!r} parameter {parameter.name!r} could not be "
                f"compiled from annotation {annotation!r} and declaration "
                f"{declaration!r}: {exc}\n"
                f"Handler: {_format_function_signature(function)}"
            )
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


def _resolve_metric_parameters(
    *,
    name: str,
    signature: inspect.Signature,
    hints: dict[str, Any],
) -> list[tuple[inspect.Parameter, Any]]:
    resolved_parameters: list[tuple[inspect.Parameter, Any]] = []
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
            resolved_parameters.append((parameter, annotation))
            continue
        if annotation is _MISSING:
            msg = f"Metric {name!r} input {parameter.name!r} must have an annotation"
            raise PluginDefinitionError(msg)
        resolved_parameters.append((parameter, annotation))
    return resolved_parameters


def _validate_input_declaration_names(
    *,
    name: str,
    function: Callable[..., Any],
    resolved_parameters: list[tuple[inspect.Parameter, Any]],
    input_declarations: dict[str, Input | BatchInput],
) -> None:
    parameter_names = {parameter.name for parameter, _annotation in resolved_parameters}
    spatial_names = {
        parameter.name
        for parameter, annotation in resolved_parameters
        if parameter.name != "context" and _spatial_marker(annotation) is not None
    }
    author_owned_names = parameter_names - spatial_names - {"context"}
    declaration_names = set(input_declarations)
    unknown_names = sorted(declaration_names - parameter_names)
    lyra_owned_names = sorted(declaration_names & (spatial_names | {"context"}))
    missing_names = sorted(author_owned_names - declaration_names)
    if unknown_names or lyra_owned_names or missing_names:
        problems: list[str] = []
        if unknown_names:
            problems.append(f"unknown declaration(s): {', '.join(unknown_names)}")
        if lyra_owned_names:
            problems.append(
                "Lyra-owned input(s) must not be declared: "
                + ", ".join(lyra_owned_names)
            )
        if missing_names:
            problems.append(f"missing declaration(s): {', '.join(missing_names)}")
        details = "\n- ".join(problems)
        msg = (
            f"Metric {name!r} input declaration mismatch:\n- {details}\n"
            f"Handler: {_format_function_signature(function)}"
        )
        raise PluginDefinitionError(msg)


def _validate_batch_runtime_value(
    metric_name: str,
    field_name: str,
    value: JsonValue,
    batch: BatchInput,
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
        if not batch.allow_labels and "label" in item:
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


def _format_function_signature(function: Callable[..., Any]) -> str:
    signature = inspect.signature(function)
    parts: list[str] = []
    added_keyword_separator = False
    for parameter in signature.parameters.values():
        if (
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and not added_keyword_separator
        ):
            parts.append("*")
            added_keyword_separator = True
        text = parameter.name
        if parameter.annotation is not _MISSING:
            text += f": {_format_annotation(parameter.annotation)}"
        if parameter.default is not _MISSING:
            text += f" = {parameter.default!r}"
        parts.append(text)
    function_name = getattr(function, "__name__", type(function).__name__)
    rendered = f"{function_name}({', '.join(parts)})"
    if signature.return_annotation is not _MISSING:
        rendered += f" -> {_format_annotation(signature.return_annotation)}"
    return rendered


def _format_annotation(annotation: PythonAnnotation) -> str:
    if isinstance(annotation, str):
        return annotation
    spatial = _spatial_marker(annotation)
    if spatial is not None:
        return "LocationInput" if spatial.kind == "location" else "BoundsInput"
    return (
        inspect.formatannotation(annotation)
        .replace("typing.", "")
        .replace("lyra.sdk.plugin.", "")
    )


__all__ = [
    "BatchInput",
    "BatchItem",
    "BoundsInput",
    "Input",
    "LocationInput",
    "MetricDefinition",
    "MetricDescription",
    "PluginDefinition",
    "PluginDefinitionError",
]
