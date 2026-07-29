from __future__ import annotations

import importlib
import importlib.abc
import inspect
import math
import multiprocessing
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from lyra.sdk import DatabaseNotConfiguredError, LyraDB, StubLyraDB, postgres
from lyra.sdk.db_types import Bounds
from lyra.sdk.postgres import PostgresLyraDB
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from collections.abc import Callable


def _assert_dependency_light_import() -> None:
    blocked = {"geopandas", "psycopg", "sqlalchemy"}

    class BlockDatabaseImports(importlib.abc.MetaPathFinder):
        def __init__(self, blocked_modules: set[str]) -> None:
            self.blocked_modules = blocked_modules

        def find_spec(
            self,
            fullname: str,
            path: object = None,
            target: object = None,
        ) -> None:
            del path, target
            if fullname.split(".", maxsplit=1)[0] in self.blocked_modules:
                message = f"blocked optional dependency: {fullname}"
                raise ModuleNotFoundError(message)

    for module_name in tuple(sys.modules):
        if (
            module_name.startswith("lyra.sdk")
            or module_name.split(".", 1)[0] in blocked
        ):
            del sys.modules[module_name]
    sys.meta_path.insert(0, BlockDatabaseImports(blocked))

    sdk = importlib.import_module("lyra.sdk")

    assert issubclass(sdk.DatabaseNotConfiguredError, RuntimeError)
    assert issubclass(sdk.StubLyraDB, sdk.LyraDB)
    assert blocked.isdisjoint(sys.modules)


def _assert_postgres_import_explains_missing_extra() -> None:
    blocked = {"geopandas"}

    class BlockDatabaseImports(importlib.abc.MetaPathFinder):
        def __init__(self, blocked_modules: set[str]) -> None:
            self.blocked_modules = blocked_modules

        def find_spec(
            self,
            fullname: str,
            path: object = None,
            target: object = None,
        ) -> None:
            del path, target
            if fullname.split(".", maxsplit=1)[0] in self.blocked_modules:
                message = f"blocked optional dependency: {fullname}"
                raise ModuleNotFoundError(message, name=fullname)

    for module_name in tuple(sys.modules):
        if (
            module_name.startswith("lyra.sdk")
            or module_name.split(".", maxsplit=1)[0] in blocked
        ):
            del sys.modules[module_name]
    sys.meta_path.insert(0, BlockDatabaseImports(blocked))

    importlib.import_module("lyra.sdk")
    with pytest.raises(ModuleNotFoundError) as error:
        importlib.import_module("lyra.sdk.postgres")

    assert "lyra-sdk[postgres]" in str(error.value)
    assert "uv add" in str(error.value)


def test_lyra_db_is_abstract_and_has_no_backend_constructor() -> None:
    assert inspect.isabstract(LyraDB)
    assert LyraDB.__init__ is object.__init__
    with pytest.raises(TypeError, match="abstract"):
        LyraDB()


def test_postgres_database_implementation_satisfies_sdk_interface() -> None:
    assert issubclass(PostgresLyraDB, LyraDB)
    for method_name in (
        "load_denue_from_bounds",
        "load_mesh_from_bounds",
        "load_census_from_bounds",
    ):
        interface_method = getattr(LyraDB, method_name)
        implementation_method = getattr(PostgresLyraDB, method_name)
        interface_signature = inspect.signature(interface_method)
        implementation_signature = inspect.signature(implementation_method)
        interface_parameters = [
            (parameter.name, parameter.kind, parameter.default)
            for parameter in interface_signature.parameters.values()
        ]
        implementation_parameters = [
            (parameter.name, parameter.kind, parameter.default)
            for parameter in implementation_signature.parameters.values()
        ]
        assert implementation_parameters == interface_parameters


def test_postgres_database_uses_but_does_not_dispose_injected_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.dialect = postgresql.dialect()
    expected = object()
    loader = MagicMock(return_value=expected)
    monkeypatch.setattr(postgres, "_load_geometries_from_bounds", loader)

    result = PostgresLyraDB(engine).load_mesh_from_bounds(
        Bounds(0, 0, 1, 1),
        level=8,
    )

    assert result is expected
    loader.assert_called_once_with(
        bounds_parameters={"xmin": 0.0, "ymin": 0.0, "xmax": 1.0, "ymax": 1.0},
        conn=connection,
        columns=["codigo", "geometry"],
        table_name="mesh_level_8",
        schema="public",
    )
    engine.connect.assert_called_once_with()
    engine.dispose.assert_not_called()


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (
            lambda database: database.load_denue_from_bounds(
                Bounds(0, 0, 1, 1),
                year=2019,
                month=11,
            ),
            "DENUE year",
        ),
        (
            lambda database: database.load_denue_from_bounds(
                Bounds(0, 0, 1, 1),
                year=2025,
                month=12,
            ),
            "DENUE month",
        ),
        (
            lambda database: database.load_mesh_from_bounds(
                Bounds(0, 0, 1, 1),
                level=3,
            ),
            "mesh level",
        ),
        (
            lambda database: database.load_census_from_bounds(
                Bounds(0, 0, 1, 1),
                level="state",
                columns=["pobtot"],
            ),
            "census level",
        ),
        (
            lambda database: database.load_census_from_bounds(
                Bounds(0, 0, 1, 1),
                level="mun",
                columns=[""],
            ),
            "non-empty",
        ),
        (
            lambda database: database.load_census_from_bounds(
                Bounds(0, 0, 1, 1),
                level="mun",
                columns=["pobtot", "pobtot"],
            ),
            "unique",
        ),
        (
            lambda database: database.load_census_from_bounds(
                Bounds(0, 0, 1, 1),
                level="mun",
                columns=["pobtot; DROP TABLE census_2020_mun"],
            ),
            "ASCII",
        ),
        (
            lambda database: database.load_census_from_bounds(
                Bounds(0, 0, 1, 1),
                level="mun",
                columns=["x" * 64],
            ),
            "63",
        ),
    ],
)
def test_postgres_database_rejects_invalid_runtime_arguments_before_connecting(
    call: Callable[[PostgresLyraDB], object],
    match: str,
) -> None:
    engine = MagicMock(spec=Engine)
    database = PostgresLyraDB(engine)

    with pytest.raises(ValueError, match=match):
        call(database)

    engine.connect.assert_not_called()


@pytest.mark.parametrize(
    ("bounds", "match"),
    [
        (Bounds(math.nan, 0, 1, 1), "finite"),
        (Bounds(0, 0, math.inf, 1), "finite"),
        (Bounds(1, 0, 1, 1), "xmin"),
        (Bounds(0, 2, 1, 1), "ymin"),
    ],
)
def test_postgres_database_rejects_invalid_bounds_before_connecting(
    bounds: Bounds,
    match: str,
) -> None:
    engine = MagicMock(spec=Engine)

    with pytest.raises(ValueError, match=match):
        PostgresLyraDB(engine).load_mesh_from_bounds(bounds)

    engine.connect.assert_not_called()


@pytest.mark.parametrize("schema", ["", "bad-schema", "x" * 64])
def test_postgres_database_rejects_invalid_schema_before_connecting(
    schema: str,
) -> None:
    engine = MagicMock(spec=Engine)

    with pytest.raises(ValueError, match=r"identifier|63|ASCII"):
        PostgresLyraDB(engine, schema=schema)

    engine.connect.assert_not_called()


def test_postgres_database_preserves_columns_and_quotes_reserved_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.dialect = postgresql.dialect()
    expected = object()
    read_postgis = MagicMock(return_value=expected)
    compile_query = MagicMock(return_value="compiled query")
    monkeypatch.setattr(postgres.geopandas, "read_postgis", read_postgis)
    monkeypatch.setattr(postgres, "compile_postgres_query", compile_query)

    result = PostgresLyraDB(engine, schema="select").load_census_from_bounds(
        Bounds(0, 0, 1, 1),
        level="mun",
        columns=["from", "geometry"],
    )

    assert result is expected
    statement = compile_query.call_args.args[0]
    assert list(statement.selected_columns.keys()) == ["from", "geometry"]
    assert {table.schema for table in statement.get_final_froms()} == {"select"}
    assert read_postgis.call_args.args[0] == "compiled query"
    assert read_postgis.call_args.kwargs["params"] == {
        "xmin": 0.0,
        "ymin": 0.0,
        "xmax": 1.0,
        "ymax": 1.0,
    }


@pytest.mark.parametrize(
    ("operation", "call"),
    [
        (
            "load_denue_from_bounds",
            lambda database: database.load_denue_from_bounds(
                Bounds(0, 0, 1, 1),
                year=2025,
                month=11,
            ),
        ),
        (
            "load_mesh_from_bounds",
            lambda database: database.load_mesh_from_bounds(Bounds(0, 0, 1, 1)),
        ),
        (
            "load_census_from_bounds",
            lambda database: database.load_census_from_bounds(
                Bounds(0, 0, 1, 1),
                level="mun",
                columns=["pobtot"],
            ),
        ),
    ],
)
def test_stub_rejects_each_database_operation(
    operation: str,
    call: Callable[[StubLyraDB], object],
) -> None:
    database = StubLyraDB()

    with pytest.raises(DatabaseNotConfiguredError) as error:
        call(database)

    assert error.value.operation == operation
    assert operation in str(error.value)
    assert "real or fake LyraDB implementation" in str(error.value)


def test_core_sdk_import_does_not_load_optional_database_dependencies() -> None:
    process = multiprocessing.get_context("spawn").Process(
        target=_assert_dependency_light_import
    )
    process.start()
    process.join()

    assert process.exitcode == 0


def test_postgres_module_explains_how_to_install_missing_extra() -> None:
    process = multiprocessing.get_context("spawn").Process(
        target=_assert_postgres_import_explains_missing_extra
    )
    process.start()
    process.join()

    assert process.exitcode == 0
