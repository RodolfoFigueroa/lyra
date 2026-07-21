from typing import Literal, get_args, get_origin

from fastapi import APIRouter
from lyra.sdk.models import DataTypeSchemaInfo, DataTypesResponse
from lyra.sdk.models.spatial import (
    CVEGEOList,
    GeoJSONBounds,
    GeoJSONLocation,
    MetZoneCode,
)
from lyra.sdk.models.strict import StrictBaseModel

router = APIRouter(tags=["Catalog"])


def extract_data_type(model_class: type[StrictBaseModel]) -> str:
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


def extract_data_type_description(model_class: type[StrictBaseModel]) -> str:
    description = getattr(model_class, "DATA_TYPE_DESCRIPTION", None)
    if isinstance(description, str) and description.strip():
        return description.strip()

    err = (
        f"Wrapper {model_class.__name__} must define a non-empty "
        "DATA_TYPE_DESCRIPTION class variable."
    )
    raise RuntimeError(err)


def build_data_type_info(
    model_class: type[StrictBaseModel],
) -> DataTypeSchemaInfo:
    return DataTypeSchemaInfo(
        data_type=extract_data_type(model_class),
        description=extract_data_type_description(model_class),
        wrapper_schema=model_class.model_json_schema(),
    )


@router.get("/data-types")
async def list_data_types() -> DataTypesResponse:
    return DataTypesResponse(
        location=[
            build_data_type_info(CVEGEOList),
            build_data_type_info(GeoJSONLocation),
            build_data_type_info(MetZoneCode),
        ],
        bounds=[
            build_data_type_info(CVEGEOList),
            build_data_type_info(GeoJSONBounds),
            build_data_type_info(MetZoneCode),
        ],
    )
