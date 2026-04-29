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
    attraction_edge_weights: Literal["length", "travel_time"]
    attraction_max_weight: float
    accessibility_edge_weights: Literal["length", "travel_time"]
    accessibility_max_weight: float
    network_type: Literal["walk", "drive"]
