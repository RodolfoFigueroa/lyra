from typing import ClassVar, Literal
from lyra.models.base import StrictBaseModel, GeoJSON


class GeoJSONWrapper(StrictBaseModel):
    DATA_TYPE_DESCRIPTION: ClassVar[str] = "A GeoDataFrame in GeoJSON format."
    data_type: Literal["geojson"]
    value: GeoJSON
