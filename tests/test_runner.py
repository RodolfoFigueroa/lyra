from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from lyra.sdk.models.job import (
    JobEnvelope,
)
from lyra.sdk.models.plugin import FileOutput, TableOutput

from lyra_app import registry
from lyra_app.config import clear_config_cache, get_config
from lyra_app.db import connection as database_connection
from lyra_app.plugins import MANIFEST_FILENAME
from tests.catalog_helpers import configure_catalog_plugins
from tests.config_helpers import load_test_config
from tests.contract_helpers import metric_manifest
from tests.plugin_helpers import plugin_config
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


def _metric(
    *, name: str, factory: str, output: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **metric_manifest(name=name, description=f"{name} metric.", output=output),
        "_factory": factory,
    }


def _manifest(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    factories = {metric["_factory"] for metric in metrics}
    assert len(factories) == 1
    return {
        "schema_version": 5,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "factory": next(iter(factories)),
        "metrics": [
            {key: value for key, value in metric.items() if key != "_factory"}
            for metric in metrics
        ],
    }


def _feature_collection(feature_id: str = "area-1") -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "id": feature_id,
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-99.20, 19.30],
                            [-99.10, 19.30],
                            [-99.10, 19.40],
                            [-99.20, 19.40],
                            [-99.20, 19.30],
                        ]
                    ],
                },
                "properties": {},
            }
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


def _table_output() -> TableOutput:
    return TableOutput.model_validate(
        {
            "kind": "table",
            "columns": [
                {
                    "name": "value",
                    "type": "integer",
                    "unit": "count",
                    "description": "Example output value.",
                }
            ],
        }
    )


def _area_output(*, nullable: bool = False) -> TableOutput:
    return TableOutput.model_validate(
        {
            "kind": "table",
            "columns": [
                {
                    "name": "covered_area_m2",
                    "type": "number",
                    "unit": "m2",
                    "description": "Covered area in square metres.",
                    "nullable": nullable,
                    "derivations": [
                        {
                            "kind": "fraction_of_location_area",
                            "name": "covered_area_fraction",
                            "description": "Fraction of the location covered.",
                        }
                    ],
                }
            ],
        }
    )


def _file_output() -> FileOutput:
    return FileOutput(
        kind="file",
        media_type="image/tiff",
        extensions=[".tif", ".tiff"],
    )


@pytest.fixture
def worker_module(tmp_path: Path) -> Iterator[ModuleType]:
    load_test_config(
        tmp_path,
        metric_queues={
            "heavy_metric": "heavy",
            "light_metric": "lightweight",
        },
    )
    worker = importlib.import_module("lyra_app.worker")
    registry.reset_catalog()
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(tmp_path / "runner-temp")
    yield worker
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)
    clear_config_cache()


def _write_module(tmp_path: Path, module_name: str, source: str) -> None:
    sys.modules.pop(module_name, None)
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")


def _write_plugin_definition(
    path: Path,
    module_name: str,
    metrics: list[dict[str, Any]],
) -> None:
    declarations = [
        (metric["name"], metric["description"], metric["output"]) for metric in metrics
    ]
    _write_module(
        path,
        module_name,
        "from lyra.sdk import (\n"
        "    LocationInput, PluginDefinition, RunContext,\n"
        "    metric as declare_metric,\n"
        ")\n"
        "from lyra.sdk.models.plugin import OutputSpec\n"
        "from pydantic import TypeAdapter\n"
        "from tests.contract_helpers import ValueParameters\n"
        f"declarations = {declarations!r}\n"
        "handlers = []\n"
        "for metric_name, description, raw_output in declarations:\n"
        "    @declare_metric(\n"
        "        name=metric_name,\n"
        "        description=description,\n"
        "        output=TypeAdapter(OutputSpec).validate_python(raw_output),\n"
        "    )\n"
        "    def metric(\n"
        "        location: LocationInput, parameters: ValueParameters, *,\n"
        "        context: RunContext\n"
        "    ):\n"
        "        raise AssertionError('metric should only be imported')\n"
        "    handlers.append(metric)\n"
        "def create_plugin():\n"
        "    return PluginDefinition(metrics=handlers)\n",
    )


def _write_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _configure_runner_plugins(path: Path) -> None:
    configure_catalog_plugins([path])


def _load_smoke_runner_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: ModuleType
) -> dict[str, Any]:
    load_test_config(
        tmp_path, metric_queues=SMOKE_METRIC_QUEUES, plugins=[SMOKE_PLUGIN_DIR]
    )
    for name in ("smoke_plugin.metrics", "smoke_plugin.plugin", "smoke_plugin"):
        sys.modules.pop(name, None)
    monkeypatch.syspath_prepend(str(SMOKE_PLUGIN_DIR))
    return worker.refresh_runner_registry("interactive")


def test_runner_loads_only_configured_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    repo = tmp_path / "repo"
    metrics = [
        _metric(name="light_metric", factory="heavy_plugin:create_plugin"),
        _metric(name="heavy_metric", factory="heavy_plugin:create_plugin"),
    ]
    _write_manifest(repo, _manifest(metrics))
    _write_plugin_definition(tmp_path, "heavy_plugin", metrics)
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(repo)

    entries = worker_module.refresh_runner_registry("heavy")

    assert list(entries) == ["heavy_metric"]
    assert entries["heavy_metric"].queue == "heavy"


def test_runner_does_not_import_plugins_for_other_queues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_module: ModuleType
) -> None:
    selected = tmp_path / "selected"
    other = tmp_path / "other"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(selected, _manifest(metrics))
    _write_plugin_definition(tmp_path, "heavy_plugin", metrics)
    other_manifest = _manifest(
        [_metric(name="light_metric", factory="uninstalled_factory:create_plugin")]
    )
    other_manifest["plugin"]["name"] = "other-plugin"
    _write_manifest(other, other_manifest)
    get_config().plugins.installed = [
        plugin_config(selected, routing={"heavy_metric": "heavy"}),
        plugin_config(other, routing={"light_metric": "lightweight"}),
    ]
    monkeypatch.syspath_prepend(str(tmp_path))

    entries = worker_module.refresh_runner_registry("heavy")

    assert list(entries) == ["heavy_metric"]
    assert "uninstalled_factory" not in sys.modules


def test_runner_loads_editable_plugin_before_api_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_module: ModuleType
) -> None:
    source = tmp_path / "editable-plugin"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(source, _manifest(metrics))
    _write_plugin_definition(source, "heavy_plugin", metrics)
    get_config().plugins.installed = [
        plugin_config(source, routing={"heavy_metric": "heavy"})
    ]
    monkeypatch.syspath_prepend(str(source))
    assert not registry.is_catalog_loaded()
    entries = worker_module.refresh_runner_registry("heavy")
    assert list(entries) == ["heavy_metric"]
    assert entries["heavy_metric"].queue == "heavy"
    assert not (tmp_path / "plugins").exists()


def test_runner_loads_smoke_editable_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_module: ModuleType
) -> None:
    entries = _load_smoke_runner_registry(tmp_path, monkeypatch, worker_module)
    assert sorted(entries) == [
        "smoke_file_metric",
        "smoke_progress_metric",
        "smoke_table_metric",
    ]
    plugin_file = sys.modules["smoke_plugin.plugin"].__file__
    assert plugin_file is not None
    assert Path(plugin_file).resolve().is_relative_to(SMOKE_PLUGIN_DIR.resolve())


def test_runner_uses_configured_worker_temp_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    config = load_test_config(
        tmp_path,
        metric_queues={
            "heavy_metric": "heavy",
            "light_metric": "lightweight",
        },
    )
    heavy_worker = config.get_worker("heavy").model_copy(
        update={"temp_dir": tmp_path / "worker-temp"},
    )
    config.workers["heavy"] = heavy_worker
    repo = tmp_path / "repo"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(repo, _manifest(metrics))
    _write_plugin_definition(tmp_path, "heavy_plugin", metrics)
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(repo)

    worker_module.refresh_runner_registry("heavy", config=config)
    context = worker_module.build_run_context(
        JobEnvelope(
            job_id="job-temp",
            metric="heavy_metric",
            input={"location": _feature_collection()},
        ),
    )

    assert context.temp_dir == tmp_path / "worker-temp" / "job-temp"
    assert context.temp_dir.is_dir()
    assert context.db is not None


def test_runner_propagates_database_context_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def fail() -> None:
        msg = "database context unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(database_connection, "get_worker_engine", fail)

    with pytest.raises(RuntimeError, match="database context unavailable"):
        worker_module.build_run_context(
            JobEnvelope(
                job_id="job-database-context",
                metric="heavy_metric",
                input={"location": _feature_collection()},
            ),
        )


def test_runner_rejects_raw_function_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    repo = tmp_path / "repo"
    metrics = [_metric(name="heavy_metric", factory="raw_plugin:run")]
    _write_manifest(repo, _manifest(metrics))
    _write_module(tmp_path, "raw_plugin", "def run(job, context):\n    return None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(repo)

    with pytest.raises(RuntimeError, match="must declare no parameters"):
        worker_module.refresh_runner_registry("heavy")


def test_runner_rejects_stale_generated_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    repo = tmp_path / "repo"
    live_metrics = [_metric(name="heavy_metric", factory="stale_plugin:create_plugin")]
    manifest_metrics = [
        {
            **live_metrics[0],
            "description": "Description changed without regeneration.",
        }
    ]
    _write_manifest(repo, _manifest(manifest_metrics))
    _write_plugin_definition(tmp_path, "stale_plugin", live_metrics)
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(repo)

    with pytest.raises(RuntimeError, match="build-manifest"):
        worker_module.refresh_runner_registry("heavy")


def test_runner_reloads_current_manifest_instead_of_api_snapshot(
    tmp_path: Path, worker_module: ModuleType
) -> None:
    source = tmp_path / "source"
    shutil.copytree(SMOKE_PLUGIN_DIR, source)
    config = load_test_config(tmp_path, plugins=[source])
    registry.initialize_catalog()
    assert registry.is_catalog_loaded()
    (source / MANIFEST_FILENAME).write_text("broken after API startup")
    with pytest.raises(RuntimeError, match="invalid"):
        worker_module.load_runner_metric_entries("interactive", config=config)
