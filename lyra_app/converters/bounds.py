import json

from lyra.sdk.models.geometry import SingleGeoJSON

from lyra_app.db.connection import engine
from lyra_app.loaders.db import (
    load_bounds_from_cvegeos,
    load_bounds_from_met_zone_code,
)


def load_from_cvegeos(cvegeos: list[str]) -> SingleGeoJSON:
    with engine.connect() as conn:
        gdf = load_bounds_from_cvegeos(cvegeos, conn=conn)
    return SingleGeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(code: str) -> SingleGeoJSON:
    with engine.connect() as conn:
        gdf = load_bounds_from_met_zone_code(code, conn=conn)
    return SingleGeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: SingleGeoJSON) -> SingleGeoJSON:
    return geojson
