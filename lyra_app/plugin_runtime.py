import importlib
import inspect
from dataclasses import dataclass
from types import FunctionType
from typing import Annotated, Any, get_args, get_origin

from lyra.sdk.models.plugin import MetricManifest
from pydantic import BaseModel, ConfigDict, create_model

from lyra_app.models import ExplicitBoundsUnion, ExplicitLocationUnion


@dataclass(frozen=True)
class RunnerMetricEntry:
    metric: MetricManifest
    model: type[BaseModel]
    params_to_convert: dict[str, list[str]]
    db_param_name: str | None
    calculate: FunctionType | None = None
    calculate_prepare: FunctionType | None = None
    calculate_for_items: FunctionType | None = None
    calculate_aggregate: FunctionType | None = None
    items_annotation: Any | None = None


def generate_model_from_func(
    func: FunctionType,
    extra_fields: dict[str, tuple] | None = None,
    model_name: str | None = None,
) -> tuple[type[BaseModel], dict[str, list[str]], str | None]:
    from lyra.sdk.db import LyraDB  # noqa: PLC0415

    sig = inspect.signature(func)
    fields = {}
    conversion_map = {}
    db_param_name = None

    for name, param in sig.parameters.items():
        annotation = param.annotation
        origin = get_origin(annotation)

        if annotation == inspect.Parameter.empty:
            msg = (
                f"Missing type hint for parameter {name!r} in function "
                f"{func.__name__!r}."
            )
            raise TypeError(msg)

        if inspect.isclass(annotation) and issubclass(annotation, LyraDB):
            db_param_name = name
            continue

        tags_found = []
        if origin is Annotated:
            metadata = get_args(annotation)[1:]

            if "REQUIRE_EXPLICIT_TYPE" in metadata:
                tags_found.append("REQUIRE_EXPLICIT_TYPE")
                annotation = ExplicitLocationUnion
            elif "REQUIRE_EXPLICIT_BOUNDS_TYPE" in metadata:
                tags_found.append("REQUIRE_EXPLICIT_BOUNDS_TYPE")
                annotation = ExplicitBoundsUnion

            if tags_found:
                conversion_map[name] = tags_found

        default_val = ... if param.default == inspect.Parameter.empty else param.default
        fields[name] = (annotation, default_val)

    if extra_fields:
        fields.update(extra_fields)

    effective_model_name = model_name or f"{func.__name__.capitalize()}RequestModel"
    model = create_model(
        effective_model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )  # ty:ignore[no-matching-overload]
    return model, conversion_map, db_param_name


def load_callable(spec: str) -> FunctionType:
    module_name, sep, attr_path = spec.partition(":")
    if not sep or not module_name or not attr_path:
        msg = f"Callable path must use 'module:attribute' format: {spec!r}"
        raise ValueError(msg)

    value: Any = importlib.import_module(module_name)
    for attr in attr_path.split("."):
        value = getattr(value, attr)

    if not isinstance(value, FunctionType):
        msg = f"Callable path {spec!r} did not resolve to a function."
        raise TypeError(msg)
    return value


def build_runner_metric_entry(metric: MetricManifest) -> RunnerMetricEntry:
    callable_spec = metric.callable
    if callable_spec.mode == "single":
        if callable_spec.calculate is None:
            msg = f"Metric {metric.name!r} is missing callable.calculate."
            raise RuntimeError(msg)
        calculate = load_callable(callable_spec.calculate)
        model, params_to_convert, db_param_name = generate_model_from_func(calculate)
        return RunnerMetricEntry(
            metric=metric,
            model=model,
            params_to_convert=params_to_convert,
            db_param_name=db_param_name,
            calculate=calculate,
        )

    if (
        callable_spec.prepare is None
        or callable_spec.for_items is None
        or callable_spec.aggregate is None
    ):
        msg = f"Metric {metric.name!r} is missing batched callable paths."
        raise RuntimeError(msg)

    prepare = load_callable(callable_spec.prepare)
    for_items = load_callable(callable_spec.for_items)
    aggregate = load_callable(callable_spec.aggregate)
    item_type = _get_items_type_from_for_items_func(for_items)
    items_annotation = dict[str, item_type] | None
    model, params_to_convert, db_param_name = generate_model_from_func(
        prepare,
        extra_fields={"items": (items_annotation, None)},
        model_name=f"{metric.name.capitalize()}RequestModel",
    )
    return RunnerMetricEntry(
        metric=metric,
        model=model,
        params_to_convert=params_to_convert,
        db_param_name=db_param_name,
        calculate_prepare=prepare,
        calculate_for_items=for_items,
        calculate_aggregate=aggregate,
        items_annotation=items_annotation,
    )


def _get_items_type_from_for_items_func(for_items_func: FunctionType) -> Any:
    params = list(inspect.signature(for_items_func).parameters.values())
    if len(params) < 2:
        msg = (
            f"{for_items_func.__name__!r} must have at least 2 parameters: "
            "item_key: str, item: <ItemType>"
        )
        raise TypeError(msg)
    item_param = params[1]
    if item_param.annotation == inspect.Parameter.empty:
        msg = (
            f"Missing type hint for parameter {item_param.name!r} "
            f"in function {for_items_func.__name__!r}."
        )
        raise TypeError(msg)
    return item_param.annotation
