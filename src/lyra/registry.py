import importlib
import pkgutil
import re

from lyra.models.wrappers import ExplicitInputUnion, ExplicitLocationAPI
import inspect
from typing import Annotated, Type, get_args, get_origin
from typing_extensions import TypedDict
from pydantic import create_model, ConfigDict, BaseModel
from types import FunctionType


TASK_REGISTRY = {}


class MetricParameterInfo(TypedDict):
    name: str
    type: str
    required: bool


class MetricInfo(TypedDict):
    name: str
    description: str
    parameters: list[MetricParameterInfo]


def generate_model_from_func(
    func: FunctionType,
    extra_fields: dict[str, tuple] | None = None,
    model_name: str | None = None,
) -> tuple[Type[BaseModel], dict[str, list[str]]]:
    sig = inspect.signature(func)
    fields = {}
    conversion_map = {}

    for name, param in sig.parameters.items():
        annotation = param.annotation
        origin = get_origin(annotation)

        if annotation == inspect._empty:
            raise TypeError(
                f"Missing type hint for parameter '{name}' "
                f"in function '{func.__name__}'."
            )

        tags_found = []
        if origin is Annotated:
            metadata = get_args(annotation)[1:]

            if "REQUIRE_EXPLICIT_TYPE" in metadata:
                tags_found.append("REQUIRE_EXPLICIT_TYPE")

                # Replace GeoJSON with the strict Pydantic Discriminator
                annotation = ExplicitInputUnion

            if tags_found:
                conversion_map[name] = tags_found

        default_val = ... if param.default == inspect._empty else param.default

        fields[name] = (annotation, default_val)

    if extra_fields:
        fields.update(extra_fields)

    effective_model_name = model_name or f"{func.__name__.capitalize()}RequestModel"
    model = create_model(
        effective_model_name, __config__=ConfigDict(extra="forbid"), **fields
    )
    return model, conversion_map


def _get_items_type_from_for_items_func(for_items_func: FunctionType):
    """Extract the item type annotation from calculate_for_items' second parameter."""
    params = list(inspect.signature(for_items_func).parameters.values())
    if len(params) < 2:
        raise TypeError(
            f"'{for_items_func.__name__}' must have at least 2 parameters: "
            "item_key: str, item: <ItemType>"
        )
    item_param = params[1]
    if item_param.annotation == inspect._empty:
        raise TypeError(
            f"Missing type hint for parameter '{item_param.name}' "
            f"in function '{for_items_func.__name__}'."
        )
    return item_param.annotation


def discover_tasks():
    # Prevent running the discovery loop multiple times if imported in multiple places
    if TASK_REGISTRY:
        return

    # Defer auth only if necessary
    from lyra.auth import initialize_earth_engine

    initialize_earth_engine()

    import lyra.processors as processors

    for _, module_name, _ in pkgutil.iter_modules(processors.__path__):
        mod = importlib.import_module(f"lyra.processors.{module_name}")

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
            raise RuntimeError(
                f"Processor '{module_name}' defines both 'calculate' and the batched "
                "pattern (calculate_prepare/calculate_for_items/calculate_aggregate). "
                "A module must define only one."
            )

        if not has_single and not has_batched:
            print(
                f"Skipping `{module_name}` as it does not have a callable 'calculate' "
                "function or the batched pattern (calculate_prepare/calculate_for_items/"
                "calculate_aggregate)."
            )
            continue

        description = getattr(mod, "METRIC_DESCRIPTION", None)
        if not isinstance(description, str) or not description.strip():
            raise RuntimeError(
                f"Processor '{module_name}' must define a non-empty "
                "METRIC_DESCRIPTION module-level string constant."
            )

        returns_file = getattr(mod, "RETURNS_FILE", False)

        if has_single:
            assert callable(calc_func)
            RequestModel, params_to_convert = generate_model_from_func(calc_func)
            TASK_REGISTRY[module_name] = {
                "calculate": calc_func,
                "model": RequestModel,
                "params_to_convert": params_to_convert,
                "description": description.strip(),
                "is_batched": False,
                "returns_file": returns_file,
            }
        else:
            assert (
                callable(prepare_func)
                and callable(for_items_func)
                and callable(aggregate_func)
            )
            item_type = _get_items_type_from_for_items_func(for_items_func)
            items_annotation = dict[str, item_type] | None
            RequestModel, params_to_convert = generate_model_from_func(
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
                "description": description.strip(),
                "is_batched": True,
                "returns_file": False,
            }


def _get_annotation_display_name(annotation) -> str:
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
    cleaned = re.sub(r"(\w+\.)+", "", type_str)
    return cleaned


def get_metrics_info() -> list[MetricInfo]:
    result = []
    for name, entry in TASK_REGISTRY.items():
        if not entry["is_batched"]:
            parameters = [
                {
                    "name": param_name,
                    "type": _get_annotation_display_name(param.annotation),
                    "required": param.default is inspect.Parameter.empty,
                }
                for param_name, param in inspect.signature(
                    entry["calculate"]
                ).parameters.items()
            ]
        else:
            parameters = [
                {
                    "name": param_name,
                    "type": _get_annotation_display_name(param.annotation),
                    "required": param.default is inspect.Parameter.empty,
                }
                for param_name, param in inspect.signature(
                    entry["calculate_prepare"]
                ).parameters.items()
            ]
            parameters.append(
                {
                    "name": "items",
                    "type": _get_annotation_display_name(entry["items_annotation"]),
                    "required": False,
                }
            )
        result.append(
            {
                "name": name,
                "description": entry["description"],
                "parameters": parameters,
            }
        )
    return result


# Run the discovery process when this file is imported
discover_tasks()
