from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import UnionType
from typing import Annotated, Literal, get_args, get_origin

from pydantic import Field
from typing_extensions import TypedDict

from lyra.models.base import GeoJSON, StrictBaseModel


class WrapperDataTypeInfo(TypedDict):
    data_type: str
    description: str


def _discover_wrapper_classes() -> list[type[StrictBaseModel]]:
    classes: list[type[StrictBaseModel]] = []

    for module_info in sorted(
        pkgutil.iter_modules(__path__),
        key=lambda item: item.name,
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


def _extract_data_type(model_class: type[StrictBaseModel]) -> str:
    field_info = model_class.model_fields.get("data_type")
    if field_info is None:
        err = f"Missing 'data_type' field in wrapper {model_class.__name__}."
        raise RuntimeError(err)

    annotation = field_info.annotation
    if get_origin(annotation) is Literal:
        args = get_args(annotation)
        if len(args) == 1 and isinstance(args[0], str):
            return args[0]

    err = (
        f"Wrapper {model_class.__name__} must declare data_type as a single "
        "Literal string value."
    )
    raise RuntimeError(err)


def _extract_data_type_description(model_class: type[StrictBaseModel]) -> str:
    description = getattr(model_class, "DATA_TYPE_DESCRIPTION", None)
    if isinstance(description, str) and description.strip():
        return description.strip()

    err = (
        f"Wrapper {model_class.__name__} must define a non-empty "
        "DATA_TYPE_DESCRIPTION class variable."
    )
    raise RuntimeError(err)


def get_wrapper_data_type_info() -> list[WrapperDataTypeInfo]:
    return [
        {
            "data_type": _extract_data_type(wrapper_class),
            "description": _extract_data_type_description(wrapper_class),
        }
        for wrapper_class in class_list
    ]


# Expose discovered classes at package level for direct imports.
for _cls in class_list:
    globals()[_cls.__name__] = _cls

if not class_list:
    err = "No wrapper classes were discovered in lyra.models.wrappers."
    raise RuntimeError(err)

_union_type = class_list[0]
for _cls in class_list[1:]:
    _union_type = _union_type | _cls

if not isinstance(_union_type, UnionType) and _union_type not in class_list:
    err = "Unable to construct ExplicitInputUnion from discovered wrappers."
    raise RuntimeError(err)

ExplicitInputUnion = Annotated[_union_type, Field(discriminator="data_type")]  # ty:ignore[invalid-type-form]
ExplicitLocationAPI = Annotated[GeoJSON, "REQUIRE_EXPLICIT_TYPE"]

__all__ = [
    "ExplicitInputUnion",
    "ExplicitLocationAPI",
    "WrapperDataTypeInfo",
    "get_wrapper_data_type_info",
    *[cls.__name__ for cls in class_list],
]
