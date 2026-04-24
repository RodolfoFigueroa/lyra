from typing import ClassVar, Literal
from pydantic import Field
from lyra.models.base import StrictBaseModel


class MetZoneNameWrapper(StrictBaseModel):
    DATA_TYPE_DESCRIPTION: ClassVar[str] = "The name of a metropolitan zone."
    data_type: Literal["met_zone_name"]
    value: str = Field(min_length=1)
