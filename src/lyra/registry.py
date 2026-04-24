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

    model_name = f"{func.__name__.capitalize()}RequestModel"
    model = create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)
    return model, conversion_map


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

        if calc_func is None or not callable(calc_func):
            print(
                f"Skipping `{module_name}` as it does not have a callable 'calculate' function."
            )
            continue

        RequestModel, params_to_convert = generate_model_from_func(calc_func)

        description = getattr(mod, "METRIC_DESCRIPTION", None)
        if not isinstance(description, str) or not description.strip():
            err = (
                f"Processor '{module_name}' must define a non-empty "
                "METRIC_DESCRIPTION module-level string constant."
            )
            raise RuntimeError(err)

        TASK_REGISTRY[module_name] = {
            "calculate": calc_func,
            "model": RequestModel,
            "params_to_convert": params_to_convert,
            "description": description.strip(),
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
    return [
        {
            "name": name,
            "description": entry["description"],
            "parameters": [
                {
                    "name": param_name,
                    "type": _get_annotation_display_name(param.annotation),
                    "required": param.default is inspect.Parameter.empty,
                }
                for param_name, param in inspect.signature(
                    entry["calculate"]
                ).parameters.items()
            ],
        }
        for name, entry in TASK_REGISTRY.items()
    ]


# Run the discovery process when this file is imported
discover_tasks()
