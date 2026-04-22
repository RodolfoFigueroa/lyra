import importlib
import pkgutil


from lyra.models import StrictBaseModel


TASK_REGISTRY = {}


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

        TASK_REGISTRY[module_name] = {"calculate": calc_func, "model": RequestModel}
        print(f"Discovered and registered metric: {module_name}")


# Run the discovery process immediately when this file is imported
discover_tasks()
