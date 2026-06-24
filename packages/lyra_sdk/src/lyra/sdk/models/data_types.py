from typing import Any

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class DataTypeSchemaInfo(StrictBaseModel):
    data_type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    wrapper_schema: dict[str, Any]


class DataTypesResponse(StrictBaseModel):
    location: list[DataTypeSchemaInfo]
    bounds: list[DataTypeSchemaInfo]


__all__ = ["DataTypeSchemaInfo", "DataTypesResponse"]
