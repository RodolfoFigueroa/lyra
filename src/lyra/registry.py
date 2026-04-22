import importlib
import pkgutil

import inspect
from lyra.models import StrictBaseModel
from typing import Type
from types import FunctionType


TASK_REGISTRY = {}


def validate_task_signature(func: FunctionType, model: Type[StrictBaseModel]) -> None:
    """
    Ensures that the calculate function and its RequestModel
    have identical parameter names and type hints.
    """
    sig = inspect.signature(func)
    func_params = sig.parameters
    model_fields = model.model_fields

    # Check for missing/extra names
    func_names = set(func_params.keys())
    model_names = set(model_fields.keys())

    if func_names != model_names:
        missing_in_func = model_names - func_names
        extra_in_func = func_names - model_names
        error_msg = f"Signature mismatch for '{func.__name__}':\n"
        if missing_in_func:
            error_msg += f" - Function is missing args: {missing_in_func}\n"
        if extra_in_func:
            error_msg += f" - Function has extra args: {extra_in_func}\n"
        raise TypeError(error_msg)

    # Check for type mismatches
    for name, param in func_params.items():
        model_type = model_fields[name].annotation
        func_type = param.annotation

        if func_type != model_type:
            raise TypeError(
                f"Type mismatch for arg '{name}' in '{func.__name__}':\n"
                f" - Model expects: {model_type}\n"
                f" - Function expects: {func_type}"
            )


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

        RequestModel = getattr(mod, "RequestModel", None)
        if RequestModel is None:
            print(
                f"Skipping `{module_name}` as it does not have a valid 'RequestModel'"
            )
            continue

        if not issubclass(RequestModel, StrictBaseModel):
            print(
                f"Skipping `{module_name}` as its 'RequestModel' is not a subclass of StrictBaseModel"
            )
            continue

        try:
            validate_task_signature(calc_func, RequestModel)
        except TypeError as e:
            print(f"Skipping `{module_name}` due to signature validation error:\n{e}")
            continue

        TASK_REGISTRY[module_name] = {"calculate": calc_func, "model": RequestModel}
        print(f"Discovered and registered metric: {module_name}")


# Run the discovery process immediately when this file is imported
discover_tasks()
