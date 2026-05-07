from typing import ClassVar, Literal

from pydantic import Field

from lyra.models.base import StrictBaseModel


class MetZoneCodeWrapper(StrictBaseModel):
    DATA_TYPE_DESCRIPTION: ClassVar[str] = "The code of a metropolitan zone."
    data_type: Literal["met_zone_code"]
    value: str = Field(min_length=1)
