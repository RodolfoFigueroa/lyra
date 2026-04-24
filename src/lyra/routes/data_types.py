from fastapi import APIRouter

from lyra.models.wrappers import WrapperDataTypeInfo, get_wrapper_data_type_info

router = APIRouter()


@router.get("/data_types", response_model=list[WrapperDataTypeInfo])
async def list_data_types() -> list[WrapperDataTypeInfo]:
    return get_wrapper_data_type_info()
