from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing_extensions import TypedDict

from lyra_app.registry import TASK_REGISTRY, _get_annotation_display_name

router = APIRouter()


class ModelFieldInfo(TypedDict):
    name: str
    type: str
    required: bool
    default: str | None


class ModelInfo(TypedDict):
    name: str
    fields: list[ModelFieldInfo]


def type_display_name(annotation: Any) -> str:
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return _get_annotation_display_name(annotation)


def extract_field_info(model_class: type[BaseModel]) -> list[ModelFieldInfo]:
    fields: list[ModelFieldInfo] = []
    for field_name, field_info in model_class.model_fields.items():
        required = field_info.is_required()
        if required:
            default = None
        elif field_info.default_factory is not None:
            default = "<factory>"
        else:
            default = repr(field_info.default)
        fields.append(
            {
                "name": field_name,
                "type": type_display_name(field_info.annotation),
                "required": required,
                "default": default,
            },
        )
    return fields


def _build_model_info(name: str, model_class: type[BaseModel]) -> ModelInfo:
    return {"name": name, "fields": extract_field_info(model_class)}


@router.get("/models")
async def list_models() -> list[ModelInfo]:
    return [
        _build_model_info(name, entry["model"]) for name, entry in TASK_REGISTRY.items()
    ]


@router.get("/models/{model_name}")
async def get_model(model_name: str) -> ModelInfo:
    entry = TASK_REGISTRY.get(model_name)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_name}' not found.",
        )
    return _build_model_info(model_name, entry["model"])
