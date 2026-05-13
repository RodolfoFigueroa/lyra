from typing import Literal, get_args, get_origin

from fastapi import APIRouter
from lyra.sdk.models import StrictBaseModel
from typing_extensions import TypedDict

from lyra_app.models.cvegeo_list import CVEGEOListWrapper
from lyra_app.models.geojson import GeoJSONWrapper, SingleGeoJSONWrapper
from lyra_app.models.met_zone_code import MetZoneCodeWrapper

router = APIRouter()


class WrapperDataTypeInfo(TypedDict):
    data_type: str
    description: str


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


@router.get("/data_types")
async def list_data_types() -> list[WrapperDataTypeInfo]:
    return [
        {
            "data_type": extract_data_type(wrapper_class),
            "description": extract_data_type_description(wrapper_class),
        }
        for wrapper_class in [
            CVEGEOListWrapper,
            GeoJSONWrapper,
            SingleGeoJSONWrapper,
            MetZoneCodeWrapper,
        ]
    ]
