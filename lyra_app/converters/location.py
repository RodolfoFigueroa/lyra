"""Conversion helpers for geographic locations."""

import json

from lyra.sdk.models.geometry import GeoJSON
from sqlalchemy.engine import Engine

from lyra_app.loaders.db import (
    load_geometries_from_cvegeos,
    load_geometries_from_met_zone_code,
)


def load_from_cvegeos(cvegeos: list[str], *, engine: Engine) -> GeoJSON:
    """Resolve CVEGEO identifiers to their full database geometries.

    Returns:
        A GeoJSON feature collection for the requested identifiers.
    """
    with engine.connect() as conn:
        gdf = load_geometries_from_cvegeos(cvegeos, conn=conn)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(code: str, *, engine: Engine) -> GeoJSON:
    """Resolve a metropolitan-zone code to its constituent geometries.

    Returns:
        A GeoJSON feature collection for the metropolitan zone.
    """
    with engine.connect() as conn:
        gdf = load_geometries_from_met_zone_code(code, conn=conn)
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: GeoJSON) -> GeoJSON:
    """Accept an already explicit location geometry without conversion.

    Returns:
        The supplied GeoJSON object unchanged.
    """
    return geojson
