import importlib
import importlib.metadata
import inspect
import logging
import re
from types import FunctionType
from typing import Annotated, Any, get_args, get_origin

from lyra.sdk.types import ExplicitLocationAPI
from pydantic import BaseModel, ConfigDict, create_model
from typing_extensions import TypedDict

from lyra_app.models import ExplicitBoundsUnion, ExplicitLocationUnion

TASK_REGISTRY = {}

logger = logging.getLogger(__name__)

PLUGINS_TARGET_DIR = "/lyra_plugins"


class MetricParameterInfo(TypedDict):
    name: str
    type: str
    required: bool


class MetricInfo(TypedDict):
    name: str
    description: str
    parameters: list[MetricParameterInfo]
    returns_file: bool


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
            err = (
                f"Missing type hint for parameter '{name}' in function "
                f"'{func.__name__}'."
            )
            raise TypeError(err)

        # LyraDB params are injected by the worker — exclude from client model
        if inspect.isclass(annotation) and issubclass(annotation, LyraDB):
            db_param_name = name
            continue

        tags_found = []
        if origin is Annotated:
            metadata = get_args(annotation)[1:]

            if "REQUIRE_EXPLICIT_TYPE" in metadata:
                tags_found.append("REQUIRE_EXPLICIT_TYPE")

                # Replace GeoJSON with the strict Pydantic Discriminator
                annotation = ExplicitLocationUnion
            elif "REQUIRE_EXPLICIT_BOUNDS_TYPE" in metadata:
                tags_found.append("REQUIRE_EXPLICIT_BOUNDS_TYPE")

                # Replace GeoJSON with the strict Pydantic Discriminator
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
    )
    return model, conversion_map, db_param_name


def _get_items_type_from_for_items_func(for_items_func: FunctionType) -> Any:
    """Extract the item type annotation from calculate_for_items' second parameter."""
    params = list(inspect.signature(for_items_func).parameters.values())
    if len(params) < 2:
        err = (
            f"'{for_items_func.__name__}' must have at least 2 parameters: "
            "item_key: str, item: <ItemType>",
        )
        raise TypeError(err)
    item_param = params[1]
    if item_param.annotation == inspect.Parameter.empty:
        err = (
            f"Missing type hint for parameter '{item_param.name}' "
            f"in function '{for_items_func.__name__}'.",
        )
        raise TypeError(err)
    return item_param.annotation


def discover_tasks() -> None:
    # Prevent running the discovery loop multiple times if imported in multiple places
    if TASK_REGISTRY:
        return

    # Defer auth only if necessary
    from lyra_app.auth import initialize_earth_engine  # noqa: PLC0415
    from lyra_app.plugins import load_plugins  # noqa: PLC0415

    initialize_earth_engine()
    load_plugins()

    for ep in importlib.metadata.entry_points(group="lyra"):
        module_name = ep.name

        if module_name in TASK_REGISTRY:
            err = (
                f"Plugin name conflict: '{module_name}' is registered by more than "
                "one package. Each plugin must have a unique entry point name."
            )
            raise RuntimeError(err)

        mod = ep.load()

        calc_func = getattr(mod, "calculate", None)
        prepare_func = getattr(mod, "calculate_prepare", None)
        for_items_func = getattr(mod, "calculate_for_items", None)
        aggregate_func = getattr(mod, "calculate_aggregate", None)

        has_single = callable(calc_func)
        has_batched = (
            callable(prepare_func)
            and callable(for_items_func)
            and callable(aggregate_func)
        )

        if has_single and has_batched:
            err = (
                f"Processor '{module_name}' defines both 'calculate' and the batched "
                "pattern (calculate_prepare/calculate_for_items/calculate_aggregate). "
                "A module must define only one.",
            )
            raise RuntimeError(err)

        if not has_single and not has_batched:
            logger.warning(
                "Skipping `%s` as it does not have a callable 'calculate' "
                "function or the batched pattern (calculate_prepare/"
                "calculate_for_items/calculate_aggregate).",
                module_name,
            )
            continue

        description = getattr(mod, "METRIC_DESCRIPTION", None)
        if not isinstance(description, str) or not description.strip():
            err = (
                f"Processor '{module_name}' must define a non-empty "
                "METRIC_DESCRIPTION module-level string constant.",
            )
            raise RuntimeError(err)

        returns_file = getattr(mod, "RETURNS_FILE", False)

        if has_single:
            # ty complains if we don't explicitly check for callable here, even
            # though we do above
            if not callable(calc_func):
                err = (
                    f"Processor '{module_name}' must define a callable 'calculate' "
                    "function.",
                )
                raise RuntimeError(err)

            RequestModel, params_to_convert, db_param_name = generate_model_from_func(  # noqa: N806
                calc_func,
            )
            TASK_REGISTRY[module_name] = {
                "calculate": calc_func,
                "model": RequestModel,
                "params_to_convert": params_to_convert,
                "db_param_name": db_param_name,
                "description": description.strip(),
                "is_batched": False,
                "returns_file": returns_file,
            }
        else:
            if not (
                callable(prepare_func)
                and callable(for_items_func)
                and callable(aggregate_func)
            ):
                err = (
                    f"Processor '{module_name}' must define callable functions for the "
                    "batched pattern: 'calculate_prepare', 'calculate_for_items', and "
                    "'calculate_aggregate'."
                )
                raise RuntimeError(err)

            item_type = _get_items_type_from_for_items_func(for_items_func)
            items_annotation = dict[str, item_type] | None
            RequestModel, params_to_convert, db_param_name = generate_model_from_func(  # noqa: N806
                prepare_func,
                extra_fields={"items": (items_annotation, None)},
                model_name=f"{module_name.capitalize()}RequestModel",
            )
            TASK_REGISTRY[module_name] = {
                "calculate_prepare": prepare_func,
                "calculate_for_items": for_items_func,
                "calculate_aggregate": aggregate_func,
                "items_default": getattr(mod, "ITEMS_DEFAULT", None),
                "items_annotation": items_annotation,
                "model": RequestModel,
                "params_to_convert": params_to_convert,
                "db_param_name": db_param_name,
                "description": description.strip(),
                "is_batched": True,
                "returns_file": False,
            }


def _get_annotation_display_name(annotation: Any) -> str:
    """Convert a parameter annotation to a human-readable type name.

    Strips module prefixes and returns just the class/type name.
    Examples:
      typing.Optional[...] -> Optional[...]
      lyra.models.base.GeoJSON -> GeoJSON
      ExplicitLocationAPI -> ExplicitLocationAPI (special case for known aliases)
    """
    if annotation is ExplicitLocationAPI or annotation == ExplicitLocationAPI:
        return "ExplicitLocationAPI"

    # Convert to string and strip module prefixes (e.g., typing.Optional -> Optional)
    type_str = str(annotation)
    # Replace patterns like "word.word.word" with just "word" (the last component)
    return re.sub(r"(\w+\.)+", "", type_str)


def get_metric_info(name: str) -> MetricInfo | None:
    all_metrics = get_metrics_info()
    return next((m for m in all_metrics if m["name"] == name), None)


def get_metrics_info() -> list[MetricInfo]:
    result = []
    for name, entry in TASK_REGISTRY.items():
        if not entry["is_batched"]:
            param_source = entry["calculate"]
            parameters = []
        else:
            param_source = entry["calculate_prepare"]
            parameters = [
                {
                    "name": "items",
                    "type": _get_annotation_display_name(entry["items_annotation"]),
                    "required": False,
                },
            ]

        parameters.extend(
            [
                {
                    "name": param_name,
                    "type": _get_annotation_display_name(param.annotation),
                    "required": param.default is inspect.Parameter.empty,
                }
                for param_name, param in inspect.signature(
                    param_source,
                ).parameters.items()
            ],
        )

        result.append(
            {
                "name": name,
                "description": entry["description"],
                "parameters": parameters,
                "returns_file": entry.get("returns_file", False),
            },
        )
    return result


# Run the discovery process when this file is imported
discover_tasks()
