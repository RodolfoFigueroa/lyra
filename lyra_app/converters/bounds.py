"""Conversion helpers for geographic bounding boxes."""

import json

from lyra.sdk.models.geometry import SingleGeoJSON
from lyra.sdk.postgres_sql import (
    DEFAULT_POSTGRES_SCHEMA,
    validate_postgres_identifier,
)
from sqlalchemy.engine import Engine

from lyra_app.loaders.db import (
    get_table_name_for_cvegeos,
    load_bounds_from_cvegeos,
    load_bounds_from_met_zone_code,
)


def load_from_cvegeos(
    cvegeos: list[str],
    *,
    engine: Engine,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> SingleGeoJSON:
    """Resolve CVEGEO identifiers to one combined bounding geometry.

    Returns:
        A single-feature GeoJSON bounding the requested identifiers.
    """
    get_table_name_for_cvegeos(cvegeos)
    validated_schema = validate_postgres_identifier(
        schema,
        field_name="database schema",
    )
    with engine.connect() as conn:
        gdf = load_bounds_from_cvegeos(
            cvegeos,
            conn=conn,
            schema=validated_schema,
        )
    return SingleGeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(
    code: str,
    *,
    engine: Engine,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> SingleGeoJSON:
    """Resolve a metropolitan-zone code to one combined bounding geometry.

    Returns:
        A single-feature GeoJSON bounding the metropolitan zone.
    """
    validated_schema = validate_postgres_identifier(
        schema,
        field_name="database schema",
    )
    with engine.connect() as conn:
        gdf = load_bounds_from_met_zone_code(
            code,
            conn=conn,
            schema=validated_schema,
        )
    return SingleGeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: SingleGeoJSON) -> SingleGeoJSON:
    """Accept an already explicit bounds geometry without conversion.

    Returns:
        The supplied single-feature GeoJSON object unchanged.
    """
    return geojson
