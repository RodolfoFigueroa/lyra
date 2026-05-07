from typing import ClassVar, Literal

from lyra.models.base import GeoJSON, SingleGeoJSON, StrictBaseModel


class GeoJSONWrapper(StrictBaseModel):
    DATA_TYPE_DESCRIPTION: ClassVar[str] = "A GeoDataFrame in GeoJSON format."
    data_type: Literal["geojson"]
    value: GeoJSON


class SingleGeoJSONWrapper(StrictBaseModel):
    DATA_TYPE_DESCRIPTION: ClassVar[str] = (
        "A GeoDataFrame in GeoJSON format containing a single geometry. "
        "Does not support MultiPolygon or GeometryCollection."
    )
    data_type: Literal["geojson"]
    value: SingleGeoJSON


__all__ = [
    "GeoJSONWrapper",
    "SingleGeoJSONWrapper",
]
