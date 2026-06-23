from typing import Any

from fastapi import APIRouter, HTTPException
from typing_extensions import TypedDict

from lyra_app.registry import (
    TASK_REGISTRY,
    ensure_catalog_loaded,
    get_metric_parameters,
)

router = APIRouter()


class ModelFieldInfo(TypedDict):
    name: str
    type: str
    required: bool
    default: Any | None


class ModelInfo(TypedDict):
    name: str
    fields: list[ModelFieldInfo]


def _build_model_info(name: str) -> ModelInfo:
    parameters = get_metric_parameters(name)
    if parameters is None:
        raise KeyError(name)
    return {
        "name": name,
        "fields": [
            {
                "name": parameter.name,
                "type": parameter.type,
                "required": parameter.required,
                "default": parameter.default,
            }
            for parameter in parameters
        ],
    }


@router.get("/models")
async def list_models() -> list[ModelInfo]:
    ensure_catalog_loaded()
    return [_build_model_info(name) for name in TASK_REGISTRY]


@router.get("/models/{model_name}")
async def get_model(model_name: str) -> ModelInfo:
    try:
        return _build_model_info(model_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_name}' not found.",
        ) from exc
