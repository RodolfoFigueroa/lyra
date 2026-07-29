from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest
from lyra.sdk import (
    DatabaseQueryError,
    DatabaseUnavailableError,
    LocalRunContext,
    postgres,
)
from lyra.sdk.db_types import Bounds
from lyra.sdk.postgres import PostgresLyraDB, connect_postgres
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _engine() -> MagicMock:
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = 1
    return engine


def test_connector_applies_local_profile_preserves_query_and_disposes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    captured: dict[str, object] = {}

    def create_engine(url: object, **options: object) -> Engine:
        captured["url"] = url
        captured.update(options)
        return engine

    monkeypatch.setattr(postgres, "create_engine", create_engine)
    source_url = make_url(
        "postgresql+psycopg://author:sensitive@db.example/lyra"
        "?sslmode=require&options=-c%20geqo=off&application_name=caller"
    )

    with connect_postgres(source_url) as database:
        assert isinstance(database, PostgresLyraDB)
        assert "sensitive" not in repr(database)
        engine.dispose.assert_not_called()

    profiled_url = cast("URL", captured["url"])
    assert profiled_url.query["sslmode"] == "require"
    assert profiled_url.query["application_name"] == "lyra-sdk-local"
    assert profiled_url.query["options"] == (
        "-c geqo=off -c default_transaction_read_only=on -c statement_timeout=300000"
    )
    assert captured["pool_size"] == 1
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 5
    assert captured["pool_recycle"] == 900
    assert captured["pool_pre_ping"] is True
    assert captured["hide_parameters"] is True
    assert captured["connect_args"] == {"connect_timeout": 5}
    engine.connect.assert_called_once_with()
    execute = engine.connect.return_value.__enter__.return_value.execute
    execute.assert_called_once()
    assert str(execute.call_args.args[0]) == "SELECT 1"
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("connect_timeout_seconds", 0),
        ("pool_timeout_seconds", -1),
        ("statement_timeout_ms", float("nan")),
        ("pool_recycle_seconds", True),
    ],
)
def test_connector_rejects_invalid_profile_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: object,
) -> None:
    create_engine = MagicMock()
    monkeypatch.setattr(postgres, "create_engine", create_engine)
    options = cast("Any", {option: value})

    with (
        pytest.raises(
            ValueError,
            match=f"{option} must be a positive number",
        ),
        connect_postgres(
            "postgresql+psycopg://author:sensitive@db.example/lyra",
            **options,
        ),
    ):
        pytest.fail("invalid connector configuration was yielded")

    create_engine.assert_not_called()


def test_connector_rejects_malformed_url_without_exposing_credentials() -> None:
    database_url = "mysql://author:sensitive-password@db.example/lyra"

    with (
        pytest.raises(
            ValueError,
            match="database_url must use PostgreSQL",
        ) as error,
        connect_postgres(database_url),
    ):
        pytest.fail("invalid connector URL was yielded")

    assert "PostgreSQL" in str(error.value)
    assert "sensitive-password" not in str(error.value)
    assert error.value.__cause__ is None


def test_connector_rejects_arbitrary_engine_options() -> None:
    options = cast("Any", {"echo": True})

    with (
        pytest.raises(TypeError, match=r"unexpected.*echo"),
        connect_postgres(
            "postgresql+psycopg://localhost/lyra",
            **options,
        ),
    ):
        pytest.fail("connector with arbitrary engine options was yielded")


def test_connector_disposes_when_adapter_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    monkeypatch.setattr(postgres, "create_engine", MagicMock(return_value=engine))

    with (
        pytest.raises(ValueError, match="database schema"),
        connect_postgres(
            "postgresql+psycopg://localhost/lyra",
            schema="invalid-schema",
        ),
    ):
        pytest.fail("invalid adapter was yielded")

    engine.connect.assert_not_called()
    engine.dispose.assert_called_once_with()


def test_connector_disposes_and_classifies_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    engine.connect.return_value.__enter__.side_effect = SQLAlchemyTimeoutError
    monkeypatch.setattr(postgres, "create_engine", MagicMock(return_value=engine))

    with (
        pytest.raises(
            DatabaseUnavailableError,
        ) as error,
        connect_postgres("postgresql+psycopg://localhost/lyra"),
    ):
        pytest.fail("failed probe was yielded")

    assert isinstance(error.value.__cause__, SQLAlchemyTimeoutError)
    engine.dispose.assert_called_once_with()


def test_connector_disposes_when_caller_execution_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    monkeypatch.setattr(postgres, "create_engine", MagicMock(return_value=engine))

    message = "plugin failed"
    with (
        pytest.raises(RuntimeError, match=message),
        connect_postgres("postgresql+psycopg://localhost/lyra"),
    ):
        raise RuntimeError(message)

    engine.dispose.assert_called_once_with()


def test_connected_adapter_retains_query_error_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    monkeypatch.setattr(postgres, "create_engine", MagicMock(return_value=engine))
    loader = MagicMock(side_effect=RuntimeError("backend detail"))
    monkeypatch.setattr(postgres, "_load_geometries_from_bounds", loader)

    with (
        connect_postgres("postgresql+psycopg://localhost/lyra") as database,
        pytest.raises(DatabaseQueryError) as error,
    ):
        database.load_mesh_from_bounds(Bounds(0, 0, 1, 1))

    assert isinstance(error.value.__cause__, RuntimeError)
    assert "backend detail" not in str(error.value)
    engine.dispose.assert_called_once_with()


def test_local_context_composes_connector_and_releases_on_plugin_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = MagicMock(spec=PostgresLyraDB)
    lifecycle: list[str] = []

    @contextmanager
    def fake_connector(_url: object, **options: object) -> Iterator[PostgresLyraDB]:
        assert options["schema"] == "shared_data"
        lifecycle.append("opened")
        try:
            yield database
        finally:
            lifecycle.append("closed")

    monkeypatch.setattr(postgres, "connect_postgres", fake_connector)

    def run_plugin() -> None:
        with LocalRunContext.connect_postgres(
            "postgresql+psycopg://localhost/lyra",
            job_id="local-job",
            metric="example",
            temp_dir=tmp_path,
            schema="shared_data",
        ) as context:
            assert context.db is database
            assert lifecycle == ["opened"]
            message = "plugin failed"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="plugin failed"):
        run_plugin()

    assert lifecycle == ["opened", "closed"]


def test_plain_local_context_keeps_injected_database_caller_owned(
    tmp_path: Path,
) -> None:
    database = MagicMock()

    context = LocalRunContext(
        job_id="local-job",
        metric="example",
        temp_dir=tmp_path,
        db=database,
    )

    assert context.db is database
    database.dispose.assert_not_called()
