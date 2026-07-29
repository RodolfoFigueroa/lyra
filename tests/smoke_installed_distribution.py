"""Smoke-test installed Lyra distributions without repository imports."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import create_autospec

from lyra.sdk import (
    Bounds,
    DatabaseNotConfiguredError,
    LocalRunContext,
    LyraDB,
    RunContext,
)


def smoke_core_sdk() -> None:
    """Exercise dependency-light local contexts from an installed core SDK."""
    assert importlib.util.find_spec("lyra_app") is None
    assert importlib.util.find_spec("geopandas") is None
    assert importlib.util.find_spec("psycopg") is None
    assert importlib.util.find_spec("sqlalchemy") is None

    with TemporaryDirectory() as directory:
        temp_dir = Path(directory)
        context = LocalRunContext(
            job_id="installed-core-smoke",
            metric="local_file",
            temp_dir=temp_dir,
        )

        def run_plugin(run_context: RunContext) -> None:
            output = run_context.temp_dir / "result.txt"
            output.write_text("installed core SDK", encoding="utf-8")
            run_context.report_progress(stage="write", current=1, total=1)
            run_context.report_message("File written")

        run_plugin(context)
        assert (temp_dir / "result.txt").read_text(encoding="utf-8") == (
            "installed core SDK"
        )
        assert [event.kind for event in context.events] == ["progress", "message"]

        rejected_operation: str | None = None
        try:
            context.db.load_mesh_from_bounds(Bounds(0, 0, 1, 1))
        except DatabaseNotConfiguredError as error:
            rejected_operation = error.operation
        if rejected_operation is None:
            message = "the default LocalRunContext database did not reject access"
            raise AssertionError(message)
        assert rejected_operation == "load_mesh_from_bounds"

        fake = create_autospec(LyraDB, instance=True)
        fake_context = LocalRunContext(
            job_id="installed-fake-smoke",
            metric="fake_database",
            temp_dir=temp_dir,
            db=fake,
        )
        assert fake_context.db is fake


def smoke_postgres_sdk() -> None:
    """Import every public live-database entry point from the SDK extra."""
    geopandas = importlib.import_module("geopandas")
    psycopg = importlib.import_module("psycopg")
    sqlalchemy = importlib.import_module("sqlalchemy")
    postgres = importlib.import_module("lyra.sdk.postgres")
    postgres_database = vars(postgres)["PostgresLyraDB"]
    connector = vars(postgres)["connect_postgres"]

    assert issubclass(postgres_database, LyraDB)
    assert callable(connector)
    assert callable(LocalRunContext.connect_postgres)
    assert vars(geopandas)["__version__"]
    assert vars(psycopg)["__version__"]
    assert vars(sqlalchemy)["__version__"]


def smoke_application() -> None:
    """Import the installed application and its packaged SDK dependency."""
    sdk = importlib.import_module("lyra.sdk")
    application = importlib.import_module("lyra_app")
    postgres = importlib.import_module("lyra.sdk.postgres")
    sdk_file = vars(sdk)["__file__"]
    application_file = vars(application)["__file__"]

    assert isinstance(sdk_file, str)
    assert isinstance(application_file, str)
    assert Path(sdk_file).is_file()
    assert Path(application_file).is_file()
    assert vars(postgres)["PostgresLyraDB"].__module__ == "lyra.sdk.postgres"


def main() -> None:
    """Run the requested isolated-distribution smoke test."""
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("core", "postgres", "application"))
    mode = parser.parse_args().mode
    if mode == "core":
        smoke_core_sdk()
    elif mode == "postgres":
        smoke_postgres_sdk()
    else:
        smoke_application()


if __name__ == "__main__":
    main()
