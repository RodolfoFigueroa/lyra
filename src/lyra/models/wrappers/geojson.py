from typing import Literal
from lyra.models.base import StrictBaseModel, GeoJSON


class GeoJSONWrapper(StrictBaseModel):
    data_type: Literal["geojson"]
    value: GeoJSON
