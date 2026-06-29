from typing import Any

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class DataTypeSchemaInfo(StrictBaseModel):
    """Schema metadata for one supported spatial wrapper type."""

    data_type: str = Field(min_length=1, description="Wrapper discriminator value.")
    description: str = Field(
        min_length=1,
        description="Human-readable wrapper description.",
    )
    wrapper_schema: dict[str, Any] = Field(
        description="JSON Schema accepted for this wrapper type.",
    )


class DataTypesResponse(StrictBaseModel):
    """Grouped spatial wrapper schemas returned by the API."""

    location: list[DataTypeSchemaInfo] = Field(
        description="Wrappers for one or more explicit locations.",
    )
    bounds: list[DataTypeSchemaInfo] = Field(
        description="Wrappers for one enclosing area or bounds geometry.",
    )


__all__ = ["DataTypeSchemaInfo", "DataTypesResponse"]
