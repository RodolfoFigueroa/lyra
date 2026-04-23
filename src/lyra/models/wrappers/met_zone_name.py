from typing import Literal
from pydantic import Field
from lyra.models.base import StrictBaseModel


class MetZoneNameWrapper(StrictBaseModel):
    data_type: Literal["met_zone_name"]
    value: str = Field(min_length=1)
