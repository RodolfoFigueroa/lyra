import json

from lyra.sdk.models.geometry import GeoJSON
from sqlalchemy.engine import Engine


def load_from_cvegeos(cvegeos: list[str], *, engine: Engine) -> GeoJSON:
    from lyra_app.loaders.db import load_geometries_from_cvegeos  # noqa: PLC0415

    with engine.connect() as conn:
        gdf = load_geometries_from_cvegeos(cvegeos, conn=conn)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(code: str, *, engine: Engine) -> GeoJSON:
    from lyra_app.loaders.db import load_geometries_from_met_zone_code  # noqa: PLC0415

    with engine.connect() as conn:
        gdf = load_geometries_from_met_zone_code(code, conn=conn)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: GeoJSON) -> GeoJSON:
    return geojson
