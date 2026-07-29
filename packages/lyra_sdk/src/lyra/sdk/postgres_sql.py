"""Shared SQLAlchemy construction helpers for Lyra-owned PostgreSQL queries."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import Column, MetaData, Table, quoted_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.engine import Connection
    from sqlalchemy.sql import ClauseElement

DEFAULT_POSTGRES_SCHEMA = "public"
POSTGRES_IDENTIFIER_MAX_LENGTH = 63

_SIMPLE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def validate_postgres_identifier(value: str, *, field_name: str) -> str:
    """Validate one identifier supported by Lyra's PostgreSQL query layer.

    Returns:
        The unchanged identifier, ready for dialect-aware SQLAlchemy quoting.

    Raises:
        ValueError: If the value is blank, too long, or not a simple identifier.
    """
    if not isinstance(value, str) or not value:
        msg = f"{field_name} must be a non-empty PostgreSQL identifier"
        raise ValueError(msg)
    if len(value) > POSTGRES_IDENTIFIER_MAX_LENGTH:
        msg = (
            f"{field_name} must be at most {POSTGRES_IDENTIFIER_MAX_LENGTH} characters"
        )
        raise ValueError(msg)
    if _SIMPLE_IDENTIFIER.fullmatch(value) is None:
        msg = (
            f"{field_name} must start with an ASCII letter or underscore and "
            "contain only ASCII letters, digits, and underscores"
        )
        raise ValueError(msg)
    return value


def postgres_table(
    table_name: str,
    *,
    schema: str,
    columns: Iterable[str],
) -> Table:
    """Build an unreflected, fully quoted, schema-qualified SQLAlchemy table.

    Returns:
        Table metadata suitable for composing a Lyra-owned read query.
    """
    validated_schema = validate_postgres_identifier(
        schema,
        field_name="database schema",
    )
    validated_table = validate_postgres_identifier(
        table_name,
        field_name="table name",
    )
    validated_columns = [
        validate_postgres_identifier(column, field_name="column name")
        for column in columns
    ]
    return Table(
        quoted_name(validated_table, quote=True),
        MetaData(),
        *(
            Column(quoted_name(column, quote=True))
            for column in dict.fromkeys(validated_columns)
        ),
        schema=quoted_name(validated_schema, quote=True),
    )


def compile_postgres_query(statement: ClauseElement, connection: Connection) -> str:
    """Compile a SQLAlchemy statement with the active connection's dialect.

    Returns:
        Dialect-aware textual SQL for consumers such as GeoPandas.
    """
    return str(statement.compile(dialect=connection.dialect))


__all__ = [
    "DEFAULT_POSTGRES_SCHEMA",
    "POSTGRES_IDENTIFIER_MAX_LENGTH",
    "compile_postgres_query",
    "postgres_table",
    "validate_postgres_identifier",
]
