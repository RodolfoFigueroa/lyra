import json

from lyra.functions.load.db import (
    load_bounds_from_cvegeos,
    load_bounds_from_met_zone_code,
)
from lyra.models.base import GeoJSON


def load_from_cvegeos(cvegeos: list[str]) -> GeoJSON:
    gdf = load_bounds_from_cvegeos(cvegeos)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(code: str) -> GeoJSON:
    gdf = load_bounds_from_met_zone_code(code)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: GeoJSON) -> GeoJSON:
    return geojson
