from __future__ import annotations

from unittest.mock import MagicMock

import psycopg
import pytest
from lyra.sdk import (
    DatabaseNotConfiguredError,
    DatabaseQueryError,
    DatabaseQueryTimeoutError,
    DatabaseUnavailableError,
    LyraDatabaseError,
)
from lyra.sdk.db_types import Bounds
from lyra.sdk.postgres import PostgresLyraDB, classify_postgres_error
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError


class SyntheticDriverError(Exception):
    def __init__(
        self, sqlstate: str, message: str = "internal database detail"
    ) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class SyntheticAuthenticationError(psycopg.OperationalError):
    sqlstate = "28P01"


def _dbapi_error(
    sqlstate: str,
    *,
    connection_invalidated: bool = False,
) -> DBAPIError:
    return DBAPIError(
        "SELECT * FROM private_table WHERE token = %(token)s",
        {"token": "super-secret-bound-value"},
        SyntheticDriverError(sqlstate),
        connection_invalidated=connection_invalidated,
    )


def test_sdk_database_error_hierarchy_is_public_and_dependency_light() -> None:
    assert issubclass(DatabaseNotConfiguredError, LyraDatabaseError)
    assert issubclass(DatabaseUnavailableError, LyraDatabaseError)
    assert issubclass(DatabaseQueryTimeoutError, LyraDatabaseError)
    assert issubclass(DatabaseQueryError, LyraDatabaseError)
    assert issubclass(LyraDatabaseError, RuntimeError)


@pytest.mark.parametrize(
    ("sqlstate", "expected_type"),
    [
        ("08001", DatabaseUnavailableError),
        ("08006", DatabaseUnavailableError),
        ("53300", DatabaseUnavailableError),
        ("57P01", DatabaseUnavailableError),
        ("57P02", DatabaseUnavailableError),
        ("57P03", DatabaseUnavailableError),
        ("57P04", DatabaseUnavailableError),
        ("57014", DatabaseQueryTimeoutError),
        ("28000", DatabaseQueryError),
        ("42501", DatabaseQueryError),
        ("42P01", DatabaseQueryError),
        ("42703", DatabaseQueryError),
        ("42601", DatabaseQueryError),
        ("40001", DatabaseQueryError),
        ("40P01", DatabaseQueryError),
    ],
)
def test_postgres_classifier_uses_sqlstate(
    sqlstate: str,
    expected_type: type[LyraDatabaseError],
) -> None:
    assert isinstance(classify_postgres_error(_dbapi_error(sqlstate)), expected_type)


def test_postgres_classifier_prioritizes_pool_timeout_and_invalidation() -> None:
    assert isinstance(
        classify_postgres_error(SQLAlchemyTimeoutError("pool exhausted")),
        DatabaseUnavailableError,
    )
    assert isinstance(
        classify_postgres_error(_dbapi_error("42601", connection_invalidated=True)),
        DatabaseUnavailableError,
    )


def test_postgres_classifier_preserves_sdk_errors() -> None:
    error = DatabaseQueryTimeoutError()

    assert classify_postgres_error(error) is error


def test_postgres_classifier_is_narrow_without_sqlstate() -> None:
    generic = DBAPIError("SELECT 1", {}, Exception("generic operational failure"))

    assert isinstance(classify_postgres_error(generic), DatabaseQueryError)
    assert isinstance(
        classify_postgres_error(psycopg.OperationalError("connection failed")),
        DatabaseUnavailableError,
    )
    assert isinstance(
        classify_postgres_error(SyntheticAuthenticationError("bad credentials")),
        DatabaseQueryError,
    )


def test_postgres_classifier_follows_backend_exception_chains() -> None:
    backend_error = _dbapi_error("08006")
    wrapper = RuntimeError("dataframe loader failed")
    wrapper.__cause__ = backend_error

    assert isinstance(classify_postgres_error(wrapper), DatabaseUnavailableError)


def test_postgres_adapter_exposes_only_safe_sdk_error_and_preserves_cause() -> None:
    backend_error = _dbapi_error("42P01")
    engine = MagicMock(spec=Engine)
    engine.connect.side_effect = backend_error

    with pytest.raises(DatabaseQueryError) as exc_info:
        PostgresLyraDB(engine).load_mesh_from_bounds(Bounds(0, 0, 1, 1))

    assert exc_info.value.__cause__ is backend_error
    assert str(exc_info.value) == "The database query failed."
    assert "private_table" not in str(exc_info.value)
    assert "super-secret-bound-value" not in str(exc_info.value)


def test_postgres_adapter_preserves_already_wrapped_sdk_error() -> None:
    backend_error = DatabaseUnavailableError()
    engine = MagicMock(spec=Engine)
    engine.connect.side_effect = backend_error

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        PostgresLyraDB(engine).load_mesh_from_bounds(Bounds(0, 0, 1, 1))

    assert exc_info.value is backend_error
    assert exc_info.value.__cause__ is None
