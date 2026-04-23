import importlib
import pkgutil

import inspect
from typing import Type
from pydantic import create_model, ConfigDict, BaseModel
from types import FunctionType


TASK_REGISTRY = {}


def generate_model_from_func(func: FunctionType) -> Type[BaseModel]:
    sig = inspect.signature(func)
    fields = {}

    for name, param in sig.parameters.items():
        if param.annotation == inspect._empty:
            raise TypeError(
                f"Missing type hint for parameter '{name}' "
                f"in function '{func.__name__}'."
            )

        if param.default == inspect._empty:
            default_val = ...
        else:
            default_val = param.default

        fields[name] = (param.annotation, default_val)

    model_name = f"{func.__name__.capitalize()}RequestModel"
    return create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)


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

        RequestModel = generate_model_from_func(calc_func)

        TASK_REGISTRY[module_name] = {"calculate": calc_func, "model": RequestModel}
        print(f"Discovered and registered metric: {module_name}")


# Run the discovery process immediately when this file is imported
discover_tasks()
