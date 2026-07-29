"""Conversion helpers for geographic locations."""

import json

from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.postgres_sql import (
    DEFAULT_POSTGRES_SCHEMA,
    validate_postgres_identifier,
)
from sqlalchemy.engine import Engine

from lyra_app.loaders.db import (
    get_table_name_for_cvegeos,
    load_geometries_from_cvegeos,
    load_geometries_from_met_zone_code,
)


def load_from_cvegeos(
    cvegeos: list[str],
    *,
    engine: Engine,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> GeoJSON:
    """Resolve CVEGEO identifiers to their full database geometries.

    Returns:
        A GeoJSON feature collection for the requested identifiers.
    """
    get_table_name_for_cvegeos(cvegeos)
    validated_schema = validate_postgres_identifier(
        schema,
        field_name="database schema",
    )
    with engine.connect() as conn:
        gdf = load_geometries_from_cvegeos(
            cvegeos,
            conn=conn,
            schema=validated_schema,
        )
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_met_zone_code(
    code: str,
    *,
    engine: Engine,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> GeoJSON:
    """Resolve a metropolitan-zone code to its constituent geometries.

    Returns:
        A GeoJSON feature collection for the metropolitan zone.
    """
    validated_schema = validate_postgres_identifier(
        schema,
        field_name="database schema",
    )
    with engine.connect() as conn:
        gdf = load_geometries_from_met_zone_code(
            code,
            conn=conn,
            schema=validated_schema,
        )
    return GeoJSON(**json.loads(gdf.to_json()))


def load_from_geojson(geojson: GeoJSON) -> GeoJSON:
    """Accept an already explicit location geometry without conversion.

    Returns:
        The supplied GeoJSON object unchanged.
    """
    return geojson
