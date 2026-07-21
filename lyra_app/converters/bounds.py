"""Conversion helpers for geographic bounding boxes."""

import json

from lyra.sdk.models.geometry import SingleGeoJSON
from sqlalchemy.engine import Engine

from lyra_app.loaders.db import load_bounds_from_cvegeos, load_bounds_from_met_zone_code


def load_from_cvegeos(cvegeos: list[str], *, engine: Engine) -> SingleGeoJSON:
    """Resolve CVEGEO identifiers to one combined bounding geometry.

    Returns:
        A single-feature GeoJSON bounding the requested identifiers.
    """
    with engine.connect() as conn:
        gdf = load_bounds_from_cvegeos(cvegeos, conn=conn)
    return SingleGeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(code: str, *, engine: Engine) -> SingleGeoJSON:
    """Resolve a metropolitan-zone code to one combined bounding geometry.

    Returns:
        A single-feature GeoJSON bounding the metropolitan zone.
    """
    with engine.connect() as conn:
        gdf = load_bounds_from_met_zone_code(code, conn=conn)
    return SingleGeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: SingleGeoJSON) -> SingleGeoJSON:
    """Accept an already explicit bounds geometry without conversion.

    Returns:
        The supplied single-feature GeoJSON object unchanged.
    """
    return geojson
