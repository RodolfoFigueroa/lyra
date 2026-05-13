import json

from lyra.sdk.models import GeoJSON
from lyra.utils.load.db import (
    load_geometries_from_cvegeos,
    load_geometries_from_met_zone_code,
)

from lyra_app.db import engine


def load_from_cvegeos(cvegeos: list[str]) -> GeoJSON:
    with engine.connect() as conn:
        gdf = load_geometries_from_cvegeos(cvegeos, conn=conn)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(code: str) -> GeoJSON:
    with engine.connect() as conn:
        gdf = load_geometries_from_met_zone_code(code, conn=conn)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: GeoJSON) -> GeoJSON:
    return geojson
