from __future__ import annotations

import importlib
import importlib.abc
import inspect
import multiprocessing
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from lyra.sdk import DatabaseNotConfiguredError, LyraDB, StubLyraDB, postgres
from lyra.sdk.db_types import Bounds
from lyra.sdk.postgres import PostgresLyraDB
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
    expected = object()
    loader = MagicMock(return_value=expected)
    monkeypatch.setattr(postgres, "_load_geometries_from_bounds", loader)

    result = PostgresLyraDB(engine).load_mesh_from_bounds(
        Bounds(0, 0, 1, 1),
        level=8,
    )

    assert result is expected
    loader.assert_called_once_with(
        Bounds(0, 0, 1, 1),
        conn=connection,
        columns=["codigo", "geometry"],
        table_name="mesh_level_8",
    )
    engine.connect.assert_called_once_with()
    engine.dispose.assert_not_called()


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
