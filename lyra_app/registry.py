import importlib
import importlib.metadata
import inspect
import logging
import re
import sys
from types import FunctionType
from typing import Annotated, Any, get_args, get_origin

from lyra.sdk.types import ExplicitLocationAPI
from pydantic import BaseModel, ConfigDict, create_model
from typing_extensions import TypedDict

from lyra_app.models import ExplicitBoundsUnion, ExplicitLocationUnion

TASK_REGISTRY = {}

logger = logging.getLogger(__name__)


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
    """Build a Pydantic request model from a plugin function's signature.

    Inspects each parameter of *func* and maps it to a Pydantic field.
    Parameters annotated with `LyraDB` are excluded from the model and tracked
    separately as the db injection point. Parameters annotated with
    ``REQUIRE_EXPLICIT_TYPE`` or ``REQUIRE_EXPLICIT_BOUNDS_TYPE`` are remapped
    to the corresponding strict discriminated-union type and recorded in the
    conversion map.

    Args:
        func (FunctionType): The plugin function whose signature is inspected.
        extra_fields (dict[str, tuple] | None): Additional
            ``{name: (type, default)}`` pairs appended after the function's own
            parameters. Defaults to ``None``.
        model_name (str | None): Name to give the generated model class.
            Defaults to ``{func.__name__.capitalize()}RequestModel``.

    Returns:
        tuple[type[BaseModel], dict[str, list[str]], str | None]: A
        ``(model, conversion_map, db_param_name)`` tuple where *model* is the
        generated Pydantic class, *conversion_map* maps parameter names to
        their conversion tags, and *db_param_name* is the name of the
        ``LyraDB`` parameter or ``None``.

    Raises:
        TypeError: If any parameter of *func* is missing a type annotation.
    """
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
    """Extract the item type annotation from a ``calculate_for_items`` function.

    The second parameter of the function represents a single item and must be
    typed; this type is used to build the ``items`` field annotation for the
    batched request model.

    Args:
        for_items_func (FunctionType): The ``calculate_for_items`` function
            from a batched plugin module.

    Returns:
        Any: The type annotation of the second parameter.

    Raises:
        TypeError: If the function has fewer than two parameters or if the
            second parameter lacks a type annotation.
    """
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
    """Discover and register all installed Lyra plugin tasks.

    Initialises Earth Engine authentication, loads plugins via `load_plugins`,
    then iterates over all ``lyra`` entry points. For each entry point the
    module is inspected for either a single-step ``calculate`` function or a
    three-function batched pattern (``calculate_prepare`` /
    ``calculate_for_items`` / ``calculate_aggregate``). A Pydantic request
    model is generated and the task is stored in ``TASK_REGISTRY``. This
    function is a no-op if the registry is already populated.

    Raises:
        RuntimeError: If a plugin name conflict is detected, if a module
            defines both the single and batched patterns, or if
            ``METRIC_DESCRIPTION`` is missing or empty.
    """
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
            assert callable(calc_func)
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
            # ruff: noqa: S101 on - required to narrow types for the type checker
            assert callable(prepare_func)
            assert callable(for_items_func)
            assert callable(aggregate_func)
            # ruff: noqa: S101 off

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
    """Convert a type annotation to a human-readable display name.

    Strips qualified module prefixes from the string representation so that
    only the base type name is shown. `ExplicitLocationAPI` is handled as a
    special case because it appears under several aliases.

    For example, ``typing.Optional[int]`` becomes ``Optional[int]`` and
    ``lyra.models.base.GeoJSON`` becomes ``GeoJSON``.

    Args:
        annotation (Any): A type annotation, e.g. from ``inspect.signature``.

    Returns:
        str: A simplified type name suitable for display in API responses.
    """
    if annotation is ExplicitLocationAPI or annotation == ExplicitLocationAPI:
        return "ExplicitLocationAPI"

    # Convert to string and strip module prefixes (e.g., typing.Optional -> Optional)
    type_str = str(annotation)
    # Replace patterns like "word.word.word" with just "word" (the last component)
    return re.sub(r"(\w+\.)+", "", type_str)


def get_metric_info(name: str) -> MetricInfo | None:
    """Look up a single metric's info dict by name.

    Args:
        name (str): The registered task name to look up.

    Returns:
        MetricInfo | None: The info dict for the named metric, or ``None`` if
        no task with that name is registered.
    """
    all_metrics = get_metrics_info()
    return next((m for m in all_metrics if m["name"] == name), None)


def get_metrics_info() -> list[MetricInfo]:
    """Return info dicts for every task registered in ``TASK_REGISTRY``.

    Each entry includes the task name, description, parameter list with types
    and required flags, and whether the task returns a file.

    Returns:
        list[MetricInfo]: One `MetricInfo` dict per registered task, in
        iteration order of ``TASK_REGISTRY``.
    """
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


def reload_tasks() -> None:
    """Clear and re-discover all plugin tasks.

    Evicts previously loaded plugin modules from `sys.modules`, clears the
    `importlib.metadata` distribution cache so newly installed packages are
    visible, then re-runs full task discovery. Intended to be called after
    `reload_plugins` has reinstalled changed repos.
    """
    # Collect entry-point module names before clearing the registry so we know
    # which sys.modules entries belong to plugins.
    plugin_module_names: set[str] = set()
    for ep in importlib.metadata.entry_points(group="lyra"):
        # ep.value is e.g. "my_plugin.module:attribute"
        plugin_module_names.add(ep.value.split(":")[0].split(".")[0])

    TASK_REGISTRY.clear()

    # Evict old plugin modules so re-import loads fresh code.
    for mod_name in list(sys.modules):
        if any(
            mod_name == name or mod_name.startswith(name + ".")
            for name in plugin_module_names
        ):
            del sys.modules[mod_name]

    # Clear importlib.metadata's distribution cache so newly installed packages
    # (and their entry points) are picked up by entry_points().
    importlib.metadata.MetadataPathFinder.invalidate_caches()
    # Also invalidate the import system's path-based caches so freshly installed
    # modules are importable without a process restart.
    importlib.invalidate_caches()

    discover_tasks()
