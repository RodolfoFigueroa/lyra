from __future__ import annotations

import importlib
import importlib.abc
import inspect
import multiprocessing
import sys
from typing import TYPE_CHECKING

import pytest
from lyra.sdk import DatabaseNotConfiguredError, LyraDB, StubLyraDB
from lyra.sdk.db_types import Bounds

from lyra_app.db.client import LyraDBImplicit

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


def test_lyra_db_is_abstract_and_has_no_backend_constructor() -> None:
    assert inspect.isabstract(LyraDB)
    assert LyraDB.__init__ is object.__init__
    with pytest.raises(TypeError, match="abstract"):
        LyraDB()


def test_application_database_implementation_satisfies_sdk_interface() -> None:
    assert issubclass(LyraDBImplicit, LyraDB)
    for method_name in (
        "load_denue_from_bounds",
        "load_mesh_from_bounds",
        "load_census_from_bounds",
    ):
        interface_method = getattr(LyraDB, method_name)
        implementation_method = getattr(LyraDBImplicit, method_name)
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
