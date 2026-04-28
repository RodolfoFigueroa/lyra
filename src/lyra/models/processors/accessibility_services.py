from enum import StrEnum
from typing import Literal

from pydantic import Field

from lyra.models.base import StrictBaseModel
from lyra.constants import AMENITIES_DICT

AmenityEnum = StrEnum(
    "AmenityEnum",
    {key.upper(): key for key in AMENITIES_DICT},
)


class AmenityGroupModel(StrictBaseModel):
    amenities: list[AmenityEnum] = Field(default_factory=lambda: list(AmenityEnum))
    edge_weights: Literal["length", "travel_time"]
    max_weight: float
