import json

from lyra.sdk.models.geometry import SingleGeoJSON
from sqlalchemy.engine import Engine


def load_from_cvegeos(cvegeos: list[str], *, engine: Engine) -> SingleGeoJSON:
    from lyra_app.loaders.db import load_bounds_from_cvegeos  # noqa: PLC0415

    with engine.connect() as conn:
        gdf = load_bounds_from_cvegeos(cvegeos, conn=conn)
    return SingleGeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(code: str, *, engine: Engine) -> SingleGeoJSON:
    from lyra_app.loaders.db import load_bounds_from_met_zone_code  # noqa: PLC0415

    with engine.connect() as conn:
        gdf = load_bounds_from_met_zone_code(code, conn=conn)
    return SingleGeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: SingleGeoJSON) -> SingleGeoJSON:
    return geojson
