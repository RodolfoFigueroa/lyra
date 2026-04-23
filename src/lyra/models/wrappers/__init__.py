from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import UnionType
from typing import Annotated

from pydantic import Field

from lyra.models.base import GeoJSON, StrictBaseModel


def _discover_wrapper_classes() -> list[type[StrictBaseModel]]:
    classes: list[type[StrictBaseModel]] = []

    for module_info in sorted(
        pkgutil.iter_modules(__path__), key=lambda item: item.name
    ):
        if module_info.ispkg:
            continue
        if module_info.name.startswith("_"):
            continue

        module = importlib.import_module(f".{module_info.name}", package=__name__)
        candidates = [
            member
            for _, member in inspect.getmembers(module, inspect.isclass)
            if member.__module__ == module.__name__
            and issubclass(member, StrictBaseModel)
            and member is not StrictBaseModel
        ]

        if len(candidates) != 1:
            err = (
                f"Expected exactly one StrictBaseModel subclass in module "
                f"'{module.__name__}', found {len(candidates)}."
            )
            raise RuntimeError(err)

        found_class = candidates[0]
        classes.append(found_class)

    return classes


class_list = _discover_wrapper_classes()

# Expose discovered classes at package level for direct imports.
for _cls in class_list:
    globals()[_cls.__name__] = _cls

if not class_list:
    raise RuntimeError("No wrapper classes were discovered in lyra.models.wrappers.")

_union_type = class_list[0]
for _cls in class_list[1:]:
    _union_type = _union_type | _cls

if not isinstance(_union_type, UnionType) and _union_type not in class_list:
    raise RuntimeError(
        "Unable to construct ExplicitInputUnion from discovered wrappers."
    )

ExplicitInputUnion = Annotated[_union_type, Field(discriminator="data_type")]  # ty:ignore[invalid-type-form]
ExplicitLocationAPI = Annotated[GeoJSON, "REQUIRE_EXPLICIT_TYPE"]

__all__ = [
    "ExplicitInputUnion",
    "ExplicitLocationAPI",
    *[cls.__name__ for cls in class_list],
]
