import pkgutil
import importlib

endpoint_map = {}

for module_info in pkgutil.iter_modules(__path__):
    module_name = module_info.name

    module = importlib.import_module(f".{module_name}", package=__name__)

    if hasattr(module, "calculate") and callable(module.calculate):
        endpoint_map[module_name] = module.calculate
