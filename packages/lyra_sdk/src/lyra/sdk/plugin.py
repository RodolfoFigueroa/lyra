"""Typed parameter-model authoring and explicit plugin execution interfaces."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar, Unpack, get_type_hints

from jsonschema.exceptions import ValidationError as SchemaValidationError
from lyra.sdk.context import RunContext
from lyra.sdk.errors import MetricInputError, PluginDefinitionError
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.plugin import (
    MetricManifest,
    OutputSpec,
    PluginInfo,
    PluginManifest,
    SpatialInputKind,
    validate_metric_name,
)
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.parameters import build_request_schema
from lyra.sdk.results import normalize_native_result
from lyra.sdk.schema import check_json_value, subschema_validator
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from lyra.sdk.models.job import FileJobResult, JobEnvelope, TableJobResult
    from lyra.sdk.results import ResultOptions

LocationInput = GeoJSON
BoundsInput = SingleGeoJSON
_HandlerT = TypeVar("_HandlerT", bound=Callable[..., object])
_DEFINITION_ATTRIBUTE = "__lyra_metric_definition__"


class MetricDescription(StrictBaseModel):
    """Author-facing inspection of a handler and its canonical request contract."""

    name: str
    description: str
    handler: str
    signature: str
    request_schema: dict[str, Any]
    spatial_inputs: dict[str, SpatialInputKind]
    output: OutputSpec


@dataclass(frozen=True)
class _MetricDefinition:
    function: Callable[..., object]
    parameters: type[BaseModel] | None
    accepts_context: bool
    manifest: MetricManifest


def _definition_message(name: str, detail: str) -> str:
    return f"Metric {name!r}: {detail}"


def _handler_arguments(
    name: str,
    function: Callable[..., object],
) -> tuple[type[BaseModel] | None, dict[str, SpatialInputKind], bool]:
    if (
        not inspect.isfunction(function)
        or inspect.iscoroutinefunction(function)
        or inspect.isgeneratorfunction(function)
        or inspect.isasyncgenfunction(function)
    ):
        raise PluginDefinitionError(
            _definition_message(
                name, "handler must be a synchronous, nongenerator function"
            )
        )
    hints = _handler_hints(name, function)
    parameters: type[BaseModel] | None = None
    spatial: dict[str, SpatialInputKind] = {}
    accepts_context = False
    for argument in inspect.signature(function).parameters.values():
        _validate_argument_kind(name, argument)
        key = argument.name
        annotation = hints.get(key)
        if (
            key == "parameters"
            and isinstance(annotation, type)
            and issubclass(annotation, BaseModel)
        ):
            parameters = annotation
        elif key == "location" and annotation is LocationInput:
            spatial[key] = "location"
        elif key == "bounds" and annotation is BoundsInput:
            spatial[key] = "bounds"
        elif key == "context" and annotation is RunContext:
            accepts_context = True
        else:
            raise PluginDefinitionError(
                _definition_message(
                    name, f"{key}: unsupported name or missing/incompatible annotation"
                )
            )
    if not spatial:
        raise PluginDefinitionError(
            _definition_message(
                name, "at least one location or bounds input is required"
            )
        )
    return parameters, spatial, accepts_context


def _handler_hints(name: str, function: Callable[..., object]) -> dict[str, Any]:
    try:
        # Return annotations are documentation only; do not require resolving them.
        annotations = {
            key: value
            for key, value in function.__annotations__.items()
            if key != "return"
        }
        annotation_holder = type(
            "HandlerAnnotations", (), {"__annotations__": annotations}
        )
        hints = get_type_hints(
            annotation_holder,
            globalns=getattr(function, "__globals__", {}),
            include_extras=True,
        )
    except (NameError, TypeError) as exc:
        raise PluginDefinitionError(
            _definition_message(name, f"unresolved handler annotation: {exc}")
        ) from exc
    return hints


def _validate_argument_kind(name: str, argument: inspect.Parameter) -> None:
    if argument.kind not in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }:
        raise PluginDefinitionError(
            _definition_message(name, f"{argument.name}: unsupported argument kind")
        )
    if argument.default is not inspect.Parameter.empty:
        raise PluginDefinitionError(
            _definition_message(
                name,
                f"{argument.name}: handler defaults are unsupported; use model fields",
            )
        )


def metric(
    *, name: str, description: str, output: OutputSpec
) -> Callable[[_HandlerT], _HandlerT]:
    """Register one contract while preserving the directly callable function.

    Returns:
        A decorator that validates and records the metric's definition.
    """

    def decorate(function: _HandlerT) -> _HandlerT:
        if hasattr(function, _DEFINITION_ATTRIBUTE):
            raise PluginDefinitionError(
                _definition_message(name, "handler is already decorated")
            )
        try:
            validate_metric_name(name)
            parameters, spatial, context = _handler_arguments(name, function)
            schema = build_request_schema(name, parameters, set(spatial))
            manifest = MetricManifest(
                name=name,
                description=description,
                request_schema=schema,
                spatial_inputs=spatial,
                output=output.model_copy(deep=True),
            )
        except (TypeError, ValueError) as exc:
            raise PluginDefinitionError(_definition_message(name, str(exc))) from exc
        setattr(
            function,
            _DEFINITION_ATTRIBUTE,
            _MetricDefinition(function, parameters, context, manifest),
        )
        return function

    return decorate


class PluginDefinition:
    """Explicitly registered handlers with local preparation and result checks."""

    def __init__(self, *, metrics: Sequence[Callable[..., object]]) -> None:
        """Collect decorated functions without module scanning or service access.

        Raises:
            PluginDefinitionError: If handlers are absent, undecorated, or duplicate.
        """
        definitions: dict[str, _MetricDefinition] = {}
        if not metrics:
            msg = "PluginDefinition requires at least one decorated metric."
            raise PluginDefinitionError(msg)
        for function in metrics:
            definition = getattr(function, _DEFINITION_ATTRIBUTE, None)
            if not isinstance(definition, _MetricDefinition):
                msg = "PluginDefinition accepts only explicitly decorated handlers."
                raise PluginDefinitionError(msg)
            name = definition.manifest.name
            if name in definitions:
                raise PluginDefinitionError(
                    _definition_message(name, "duplicate metric name")
                )
            definitions[name] = definition
        self._metrics = MappingProxyType(definitions)

    def _get(self, name: str) -> _MetricDefinition:
        try:
            return self._metrics[name]
        except KeyError as exc:
            available = ", ".join(self.metric_names)
            raise PluginDefinitionError(
                _definition_message(
                    name,
                    f"unknown metric; available metrics: {available}",
                )
            ) from exc

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Registered metric names in deterministic order."""
        return tuple(sorted(self._metrics))

    def describe(self, name: str) -> MetricDescription:
        """Return inspection metadata without adding it to the persisted manifest."""
        definition = self._get(name)
        function = definition.function
        qualified_name = getattr(function, "__qualname__", type(function).__qualname__)
        name = getattr(function, "__name__", type(function).__name__)
        return MetricDescription(
            **definition.manifest.model_dump(),
            handler=f"{function.__module__}.{qualified_name}",
            signature=f"{name}{inspect.signature(function)}",
        )

    def manifest(self, *, plugin: PluginInfo, factory: str) -> PluginManifest:
        """Return the sole persisted format-5 contract, without runtime state."""
        return PluginManifest(
            plugin=plugin.model_copy(deep=True),
            factory=factory,
            metrics=[
                self._metrics[name].manifest.model_copy(deep=True)
                for name in self.metric_names
            ],
        )

    def prepare_parameters(self, name: str, value: object) -> BaseModel:
        """Validate parameter JSON before parsing it through the author's model.

        Returns:
            The typed model, including validated defaults and semantic checks.

        Raises:
            PluginDefinitionError: If the metric declares no parameter model.
            MetricInputError: If the input is not valid JSON or fails validation.
        """
        definition = self._get(name)
        model = definition.parameters
        if model is None:
            raise PluginDefinitionError(
                _definition_message(name, "metric has no parameters")
            )
        schema = definition.manifest.request_schema
        properties = schema["properties"]
        if not isinstance(properties, dict) or not isinstance(
            properties["parameters"], dict
        ):
            raise PluginDefinitionError(
                _definition_message(name, "parameters schema is missing")
            )
        try:
            check_json_value(value, "parameters")
            encoded = json.dumps(value, allow_nan=False)
            subschema_validator(schema, properties["parameters"]).validate(value)
            return model.model_validate_json(encoded)
        except SchemaValidationError as exc:
            path = ".".join(["parameters", *(str(item) for item in exc.path)])
            raise MetricInputError(name, path, exc.message) from exc
        except ValidationError as exc:
            error = exc.errors(include_url=False)[0]
            path = ".".join(["parameters", *(str(item) for item in error["loc"])])
            raise MetricInputError(name, path, error["msg"]) from exc
        except (TypeError, ValueError) as exc:
            raise MetricInputError(name, "parameters", str(exc)) from exc

    def normalize_result(
        self,
        name: str,
        value: object,
        **options: Unpack[ResultOptions],
    ) -> TableJobResult | FileJobResult:
        """Return a validated terminal success result from a DataFrame or Path."""
        return normalize_native_result(
            name, self._get(name).manifest.output, value, **options
        )

    def __call__(self, job: JobEnvelope, context: RunContext) -> object:
        """Prepare resolved inputs and invoke a handler, returning its native value.

        Returns:
            The unnormalized handler result.

        Raises:
            MetricInputError: If resolved inputs do not match the declared contract.
        """
        definition = self._get(job.metric)
        expected = set(definition.manifest.spatial_inputs)
        if definition.parameters is not None:
            expected.add("parameters")
        if set(job.input) != expected:
            raise MetricInputError(
                job.metric, "input", f"expected fields {sorted(expected)}"
            )
        arguments: dict[str, Any] = {}
        if definition.parameters is not None:
            arguments["parameters"] = self.prepare_parameters(
                job.metric, job.input["parameters"]
            )
        for field in definition.manifest.spatial_inputs:
            model = GeoJSON if field == "location" else SingleGeoJSON
            try:
                arguments[field] = model.model_validate(job.input[field])
            except ValidationError as exc:
                raise MetricInputError(job.metric, field, str(exc)) from exc
        if definition.accepts_context:
            arguments["context"] = context
        return definition.function(**arguments)
