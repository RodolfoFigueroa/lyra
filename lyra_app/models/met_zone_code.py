from typing import ClassVar, Literal

from lyra.sdk.models import StrictBaseModel
from pydantic import Field


class MetZoneCodeWrapper(StrictBaseModel):
    DATA_TYPE_DESCRIPTION: ClassVar[str] = "The code of a metropolitan zone."
    data_type: Literal["met_zone_code"]
    value: str = Field(min_length=1)
