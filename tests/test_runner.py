from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import pytest
from lyra.sdk import (
    LocationInput,
    PluginDefinition,
    RunContext,
)
from lyra.sdk import (
    metric as declare_metric,
)
from lyra.sdk.models.job import (
    JobEnvelope,
    TableJobResult,
    TerminalJobResult,
)
from lyra.sdk.models.plugin import FileOutput, TableOutput
from sqlalchemy.exc import OperationalError

from lyra_app import registry, worker_control
from lyra_app.config import clear_config_cache, get_config
from lyra_app.db import connection as database_connection
from lyra_app.plugins import MANIFEST_FILENAME
from tests.catalog_helpers import configure_catalog_plugins
from tests.config_helpers import load_test_config
from tests.contract_helpers import ValueParameters, metric_manifest
from tests.plugin_helpers import plugin_config
from tests.redis_job_scripts import eval_job_script, seed_status
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
    feature_collection,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType

    from lyra.sdk.types import JsonObject

    from lyra_app.worker import RunnerMetricEntry


def _metric(
    *, name: str, factory: str, output: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **metric_manifest(name=name, description=f"{name} metric.", output=output),
        "_factory": factory,
    }


def _runner_entry(
    worker: ModuleType,
    *,
    metric_name: str,
    queue: str,
    output: TableOutput | FileOutput,
    run: Callable[[RunContext], object],
) -> RunnerMetricEntry:
    @declare_metric(name=metric_name, description="Worker fixture.", output=output)
    def handler(
        location: LocationInput, parameters: ValueParameters, context: RunContext
    ) -> object:
        del location, parameters
        return run(context)

    return worker.RunnerMetricEntry(
        metric_name=metric_name,
        queue=queue,
        definition=PluginDefinition(metrics=[handler]),
    )


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


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> None:
        if nx and key in self.values:
            return
        self.values[key] = value
        self.expirations.append((key, ex))

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | float,
    ) -> int | str:
        return eval_job_script(self, numkeys, keys_and_args, script)


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


def _decode_stored_result(
    worker: ModuleType,
    redis: FakeRedisSync,
    job_id: str,
) -> dict[str, Any]:
    stored = redis.get(worker.job_store.result_key(job_id))
    assert stored is not None
    return json.loads(stored)


def _decode_status(
    worker: ModuleType, redis: FakeRedisSync, job_id: str
) -> dict[str, Any]:
    stored = redis.get(worker.job_store.status_key(job_id))
    assert stored is not None
    return json.loads(stored)


def test_worker_registers_only_generic_task(worker_module: ModuleType) -> None:
    assert worker_module.GENERIC_TASK_NAME in worker_module.celery_app.tasks
    assert "light_metric" not in worker_module.celery_app.tasks
    assert "heavy_metric" not in worker_module.celery_app.tasks


def test_worker_task_failure_signal_notifies_by_task_id(
    worker_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notified: list[str] = []
    monkeypatch.setattr(
        worker_control,
        "notify_unexpected_task_failure",
        notified.append,
    )

    worker_module._notify_unexpected_task_failure(task_id="job-1")  # ruff:ignore[private-member-access]

    assert notified == ["job-1"]


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
        "smoke_cancel_metric",
        "smoke_file_metric",
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


def test_generic_task_executes_factory_and_persists_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(
        repo,
        _manifest(
            [
                _metric(
                    name="heavy_metric",
                    factory="success_plugin:create_plugin",
                )
            ]
        ),
    )
    _write_module(
        tmp_path,
        "success_plugin",
        "from lyra.sdk import (\n"
        "    LocationInput, PluginDefinition, RunContext, metric,\n"
        ")\n"
        "import pandas as pd\n"
        "from tests.contract_helpers import ValueParameters\n"
        "from lyra.sdk.models.plugin import TableColumn, TableOutput\n"
        "@metric(\n"
        "    name='heavy_metric',\n"
        "    description='heavy_metric metric.',\n"
        "    output=TableOutput(\n"
        "        kind='table',\n"
        "        columns=[TableColumn(\n"
        "            name='value', type='integer', unit='count',\n"
        "            description='Example output value.',\n"
        "        )],\n"
        "    ),\n"
        ")\n"
        "def run(location: LocationInput, parameters: ValueParameters, *,\n"
        "        context: RunContext):\n"
        "    assert context.metric == 'heavy_metric'\n"
        "    assert hasattr(context, 'db')\n"
        "    context.report_progress(stage='compute', current=50, total=100)\n"
        "    return pd.DataFrame(\n"
        "        index=['area-1'],\n"
        "        columns=['value'],\n"
        "        data=[[parameters.value * 2]],\n"
        "    )\n"
        "def create_plugin():\n"
        "    return PluginDefinition(metrics=[run])\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(repo)
    worker_module.refresh_runner_registry("heavy")
    worker_module.set_runner_temp_base(tmp_path / "tmp")

    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-1") is None:
        seed_status("job-1", "queued", metric="heavy_metric")
    payload = worker_module.execute_job(
        {
            "job_id": "job-1",
            "metric": "heavy_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 3}},
        },
        task_id="task-id",
    )

    assert payload == {
        "kind": "table",
        "job_id": "job-1",
        "status": "succeeded",
        "index": ["area-1"],
        "columns": ["value"],
        "data": [[6]],
    }
    assert _decode_stored_result(worker_module, fake_redis, "job-1") == payload
    descriptor = worker_module.job_store.get_job_result_descriptor(
        "job-1",
        client=fake_redis,
    )
    assert descriptor is not None
    assert descriptor.result_ref == "lyra://results/job-1"
    assert descriptor.preview.rows == [{"_result_index": "area-1", "value": 6}]
    assert _decode_stored_result(worker_module, fake_redis, "job-1") == payload
    assert _decode_status(worker_module, fake_redis, "job-1")["status"] == "succeeded"


def test_smoke_table_metric_executes_from_directory_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    _load_smoke_runner_registry(tmp_path, monkeypatch, worker_module)
    worker_module.set_runner_temp_base(tmp_path / "tmp")
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-smoke-table") is None:
        seed_status("job-smoke-table", "queued", metric="smoke_table_metric")
    payload = worker_module.execute_job(
        {
            "job_id": "job-smoke-table",
            "metric": "smoke_table_metric",
            "input": {
                "location": feature_collection(("area-1", "area-2")),
                "parameters": {"value": 7},
            },
        },
        task_id="task-id",
    )

    assert payload == {
        "kind": "table",
        "job_id": "job-smoke-table",
        "status": "succeeded",
        "index": ["area-1", "area-2"],
        "columns": ["value"],
        "data": [[7], [7]],
    }
    assert (
        _decode_stored_result(worker_module, fake_redis, "job-smoke-table") == payload
    )


def test_unknown_metric_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-unknown") is None:
        seed_status("job-unknown", "queued", metric="missing")
    result = worker_module.execute_job(
        {
            "job_id": "job-unknown",
            "metric": "missing",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "unknown_metric"
    assert _decode_stored_result(worker_module, fake_redis, "job-unknown") == result
    assert _decode_status(worker_module, fake_redis, "job-unknown")["status"] == (
        "failed"
    )


def test_invalid_job_envelope_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("task-id") is None:
        seed_status("task-id", "queued")
    result = worker_module.execute_job({"metric": "missing"}, task_id="task-id")

    assert result["job_id"] == "task-id"
    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_envelope"
    assert _decode_stored_result(worker_module, fake_redis, "task-id") == result
    assert _decode_status(worker_module, fake_redis, "task-id")["status"] == "failed"


def test_plugin_exception_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def fail(context: RunContext) -> pd.DataFrame:  # ruff:ignore[unused-function-argument]
        msg = "boom"
        raise RuntimeError(msg)

    worker_module.RUNNER_REGISTRY["bad_metric"] = _runner_entry(
        worker_module,
        metric_name="bad_metric",
        queue="heavy",
        output=_table_output(),
        run=fail,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-bad") is None:
        seed_status("job-bad", "queued", metric="bad_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-bad",
            "metric": "bad_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"] == {
        "type": "worker",
        "message": "Metric execution failed unexpectedly.",
    }
    assert _decode_stored_result(worker_module, fake_redis, "job-bad") == result


def test_database_exception_persists_retryable_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def fail(context: RunContext) -> pd.DataFrame:  # ruff:ignore[unused-function-argument]
        statement = "SELECT 1"
        message = "unavailable"
        raise OperationalError(statement, {}, Exception(message))

    worker_module.RUNNER_REGISTRY["database_metric"] = _runner_entry(
        worker_module,
        metric_name="database_metric",
        queue="heavy",
        output=_table_output(),
        run=fail,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-database") is None:
        seed_status("job-database", "queued", metric="database_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-database",
            "metric": "database_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"] == {
        "type": "database_unavailable",
        "message": "The database is temporarily unavailable.",
        "retryable": True,
    }
    assert _decode_stored_result(worker_module, fake_redis, "job-database") == result


@pytest.mark.parametrize(
    "plugin_result",
    [
        {"job_id": "job-invalid", "status": "progress"},
        TableJobResult(
            job_id="other-job",
            index=["area-1"],
            columns=["value"],
            data=[[1]],
        ),
    ],
)
def test_invalid_plugin_result_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
    plugin_result: TerminalJobResult | JsonObject,
) -> None:
    def run(
        _context: RunContext,
    ) -> TerminalJobResult | JsonObject:
        return plugin_result

    worker_module.RUNNER_REGISTRY["invalid_metric"] = _runner_entry(
        worker_module,
        metric_name="invalid_metric",
        queue="heavy",
        output=_table_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-invalid") is None:
        seed_status("job-invalid", "queued", metric="invalid_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-invalid",
            "metric": "invalid_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result["job_id"] == "job-invalid"
    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert _decode_stored_result(worker_module, fake_redis, "job-invalid") == result


@pytest.mark.parametrize(
    "plugin_result",
    [
        pd.DataFrame(index=["other-area"], columns=["value"], data=[[1]]),
        pd.DataFrame(index=["area-1"], columns=["other_value"], data=[[1]]),
        pd.DataFrame(index=["area-1"], columns=["value"], data=[["wrong"]]),
        pd.DataFrame(index=["area-1"], columns=["value"], data=[[None]]),
    ],
)
def test_invalid_table_result_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
    plugin_result: pd.DataFrame,
) -> None:
    def run(context: RunContext) -> pd.DataFrame:  # ruff:ignore[unused-function-argument]
        return plugin_result

    worker_module.RUNNER_REGISTRY["invalid_table_metric"] = _runner_entry(
        worker_module,
        metric_name="invalid_table_metric",
        queue="heavy",
        output=_table_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-invalid-table") is None:
        seed_status("job-invalid-table", "queued", metric="invalid_table_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-invalid-table",
            "metric": "invalid_table_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert _decode_stored_result(worker_module, fake_redis, "job-invalid-table") == (
        result
    )


def test_worker_appends_fractional_area_column(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def run(context: RunContext) -> pd.DataFrame:  # ruff:ignore[unused-function-argument]
        return pd.DataFrame(
            index=["area-1"], columns=["covered_area_m2"], data=[[25.0]]
        )

    worker_module.RUNNER_REGISTRY["area_metric"] = _runner_entry(
        worker_module,
        metric_name="area_metric",
        queue="heavy",
        output=_area_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-area") is None:
        seed_status("job-area", "queued", metric="area_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-area",
            "metric": "area_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
            "location_areas_m2": {"area-1": 100.0},
        },
        task_id="task-id",
    )

    assert result["status"] == "succeeded"
    assert result["columns"] == ["covered_area_m2", "covered_area_fraction"]
    assert result["data"] == [[25.0, 0.25]]


def test_worker_normalizes_fraction_within_range_tolerance(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def run(context: RunContext) -> pd.DataFrame:  # ruff:ignore[unused-function-argument]
        return pd.DataFrame(
            index=["area-1"], columns=["covered_area_m2"], data=[[100.00000005]]
        )

    worker_module.RUNNER_REGISTRY["area_metric"] = _runner_entry(
        worker_module,
        metric_name="area_metric",
        queue="heavy",
        output=_area_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-area-tolerance") is None:
        seed_status("job-area-tolerance", "queued", metric="area_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-area-tolerance",
            "metric": "area_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
            "location_areas_m2": {"area-1": 100.0},
        },
        task_id="task-id",
    )

    assert result["status"] == "succeeded"
    assert result["data"] == [[100.00000005, 1.0]]


@pytest.mark.parametrize("source_value", [-1.0, 101.0])
def test_worker_rejects_fraction_outside_unit_interval(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
    source_value: float,
) -> None:
    def run(context: RunContext) -> pd.DataFrame:  # ruff:ignore[unused-function-argument]
        return pd.DataFrame(
            index=["area-1"], columns=["covered_area_m2"], data=[[source_value]]
        )

    worker_module.RUNNER_REGISTRY["area_metric"] = _runner_entry(
        worker_module,
        metric_name="area_metric",
        queue="heavy",
        output=_area_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-area-range") is None:
        seed_status("job-area-range", "queued", metric="area_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-area-range",
            "metric": "area_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
            "location_areas_m2": {"area-1": 100.0},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert "outside [0, 1]" in result["error"]["message"]


@pytest.mark.parametrize(
    ("invalid_areas", "message"),
    [
        (None, "areas must match location feature IDs"),
        ({"other-area": 100.0}, "areas must match location feature IDs"),
    ],
)
def test_worker_propagates_nullable_fraction_and_requires_area_metadata(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
    invalid_areas: dict[str, float] | None,
    message: str,
) -> None:
    def run(context: RunContext) -> pd.DataFrame:  # ruff:ignore[unused-function-argument]
        return pd.DataFrame(
            index=["area-1"], columns=["covered_area_m2"], data=[[None]]
        )

    worker_module.RUNNER_REGISTRY["area_metric"] = _runner_entry(
        worker_module,
        metric_name="area_metric",
        queue="heavy",
        output=_area_output(nullable=True),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-area-missing") is None:
        seed_status("job-area-missing", "queued", metric="area_metric")
    missing = worker_module.execute_job(
        {
            "job_id": "job-area-missing",
            "metric": "area_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
            "location_areas_m2": invalid_areas,
        },
        task_id="task-id",
    )
    if worker_module.job_store.get_job_status("job-area-null") is None:
        seed_status("job-area-null", "queued", metric="area_metric")
    succeeded = worker_module.execute_job(
        {
            "job_id": "job-area-null",
            "metric": "area_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
            "location_areas_m2": {"area-1": 100.0},
        },
        task_id="task-id",
    )

    assert missing["status"] == "failed"
    assert message in missing["error"]["message"]
    assert succeeded["data"] == [[None, None]]


def test_duplicate_resolved_location_ids_persist_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def run(context: RunContext) -> pd.DataFrame:  # ruff:ignore[unused-function-argument]
        return pd.DataFrame(index=["area-1"], columns=["value"], data=[[1]])

    location = _feature_collection()
    location["features"].append(location["features"][0].copy())

    worker_module.RUNNER_REGISTRY["duplicate_location_metric"] = _runner_entry(
        worker_module,
        metric_name="duplicate_location_metric",
        queue="heavy",
        output=_table_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-duplicate-location") is None:
        seed_status(
            "job-duplicate-location", "queued", metric="duplicate_location_metric"
        )
    result = worker_module.execute_job(
        {
            "job_id": "job-duplicate-location",
            "metric": "duplicate_location_metric",
            "input": {"location": location, "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"] == {
        "type": "invalid_result",
        "path": "index",
        "message": (
            "unique string index must exactly match location feature IDs in order"
        ),
    }
    assert (
        _decode_stored_result(
            worker_module,
            fake_redis,
            "job-duplicate-location",
        )
        == result
    )


def test_file_result_persists_through_generic_result_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def run(context: RunContext) -> Path:
        output_path = context.temp_dir / "result.tif"
        output_path.write_bytes(b"data")
        return output_path

    worker_module.set_runner_temp_base(tmp_path / "tmp")
    worker_module.RUNNER_REGISTRY["file_metric"] = _runner_entry(
        worker_module,
        metric_name="file_metric",
        queue="heavy",
        output=_file_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-file") is None:
        seed_status("job-file", "queued", metric="file_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-file",
            "metric": "file_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result == {
        "kind": "file",
        "job_id": "job-file",
        "status": "succeeded",
        "file_path": str(tmp_path / "tmp" / "job-file" / "result.tif"),
        "media_type": "image/tiff",
    }
    assert _decode_stored_result(worker_module, fake_redis, "job-file") == result


def test_smoke_file_metric_executes_from_directory_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    _load_smoke_runner_registry(tmp_path, monkeypatch, worker_module)
    worker_module.set_runner_temp_base(tmp_path / "tmp")
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)
    expected_path = tmp_path / "tmp" / "job-smoke-file" / "smoke-result.txt"

    if worker_module.job_store.get_job_status("job-smoke-file") is None:
        seed_status("job-smoke-file", "queued", metric="smoke_file_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-smoke-file",
            "metric": "smoke_file_metric",
            "input": {"location": feature_collection(("area-1", "area-2"))},
        },
        task_id="task-id",
    )

    assert result == {
        "kind": "file",
        "job_id": "job-smoke-file",
        "status": "succeeded",
        "file_path": str(expected_path),
        "media_type": "text/plain",
    }
    assert expected_path.read_text(encoding="utf-8") == (
        "smoke file result\narea-1\narea-2\n"
    )
    assert _decode_stored_result(worker_module, fake_redis, "job-smoke-file") == result


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("result.txt", "image/tiff"),
        ("missing.tif", "image/tiff"),
    ],
)
def test_invalid_file_result_persists_failed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
    filename: str,
    media_type: str,
) -> None:
    assert media_type == "image/tiff"

    def run(context: RunContext) -> Path:
        output_path = context.temp_dir / filename
        if filename != "missing.tif":
            output_path.write_bytes(b"data")
        return output_path

    worker_module.set_runner_temp_base(tmp_path / "tmp")
    worker_module.RUNNER_REGISTRY["invalid_file_metric"] = _runner_entry(
        worker_module,
        metric_name="invalid_file_metric",
        queue="heavy",
        output=_file_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-invalid-file") is None:
        seed_status("job-invalid-file", "queued", metric="invalid_file_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-invalid-file",
            "metric": "invalid_file_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert _decode_stored_result(worker_module, fake_redis, "job-invalid-file") == (
        result
    )


def test_check_cancelled_persists_cancelled_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def run(context: RunContext) -> pd.DataFrame:
        seed_status(context.job_id, "cancelled")
        context.check_cancelled()
        return pd.DataFrame(index=["area-1"], columns=["value"], data=[[1]])

    worker_module.RUNNER_REGISTRY["cancel_metric"] = _runner_entry(
        worker_module,
        metric_name="cancel_metric",
        queue="heavy",
        output=_table_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    if worker_module.job_store.get_job_status("job-cancel") is None:
        seed_status("job-cancel", "queued", metric="cancel_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-cancel",
            "metric": "cancel_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result == {
        "kind": "cancelled",
        "job_id": "job-cancel",
        "status": "cancelled",
    }
    assert _decode_stored_result(worker_module, fake_redis, "job-cancel") == result
    assert _decode_status(worker_module, fake_redis, "job-cancel")["status"] == (
        "cancelled"
    )


def test_smoke_cancel_metric_respects_pre_cancelled_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    _load_smoke_runner_registry(tmp_path, monkeypatch, worker_module)
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)
    seed_status(
        "job-smoke-cancel",
        "queued",
        metric="smoke_cancel_metric",
    )
    seed_status(
        "job-smoke-cancel",
        "cancelled",
        metric="smoke_cancel_metric",
    )

    if worker_module.job_store.get_job_status("job-smoke-cancel") is None:
        seed_status("job-smoke-cancel", "queued", metric="smoke_cancel_metric")
    result = worker_module.execute_job(
        {
            "job_id": "job-smoke-cancel",
            "metric": "smoke_cancel_metric",
            "input": {"location": feature_collection(), "parameters": {"value": 1}},
        },
        task_id="task-id",
    )

    assert result == {
        "kind": "cancelled",
        "job_id": "job-smoke-cancel",
        "status": "cancelled",
    }
    assert _decode_stored_result(worker_module, fake_redis, "job-smoke-cancel") == (
        result
    )
    assert (
        _decode_status(worker_module, fake_redis, "job-smoke-cancel")["status"]
        == "cancelled"
    )


def test_run_context_report_progress_writes_snapshot(
    worker_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)
    context = worker_module.WorkerRunContext(
        job_id="job-1",
        metric="metric",
        logger=worker_module.logger,
        temp_dir=tmp_path,
        db=None,
    )

    seed_status("job-1", "queued", metric="metric", client=fake_redis)
    seed_status("job-1", "running", metric="metric", client=fake_redis)
    context.report_progress(stage="compute", current=50, total=100)

    snapshot = worker_module.job_store.get_job_status("job-1", client=fake_redis)
    assert snapshot is not None
    assert snapshot.progress is not None
    assert snapshot.progress.current == 50
    assert _decode_status(worker_module, fake_redis, "job-1")["status"] == "running"


def test_run_context_coalesces_progress_and_flushes_latest(
    worker_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_redis = FakeRedisSync()
    config = get_config()
    coalescing_config = config.model_copy(
        update={
            "job_progress": config.job_progress.model_copy(
                update={"min_interval_ms": 1_000}
            )
        }
    )
    monkeypatch.setattr(worker_module, "get_config", lambda: coalescing_config)
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: 100.0)
    context = worker_module.WorkerRunContext(
        job_id="job-1",
        metric="metric",
        logger=worker_module.logger,
        temp_dir=tmp_path,
        db=None,
    )
    seed_status("job-1", "queued", metric="metric", client=fake_redis)
    seed_status("job-1", "running", metric="metric", client=fake_redis)

    context.report_progress(stage="compute", current=0, total=10)
    context.report_progress(stage="compute", current=1, total=10)
    context.report_progress(stage="revised", current=0.5, total=20, unit="items")
    context.flush_progress()

    snapshot = worker_module.job_store.get_job_status("job-1", client=fake_redis)
    assert snapshot is not None
    assert snapshot.progress is not None
    assert snapshot.progress.current == pytest.approx(0.5)
    assert snapshot.progress.stage == "revised"
    assert snapshot.progress.total == 20


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


@pytest.mark.parametrize("state", ["missing", "running", "cancelled", "succeeded"])
def test_late_or_duplicate_delivery_never_invokes_plugin(
    state: str,
    worker_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", redis)
    called: list[str] = []

    def run(context: RunContext) -> pd.DataFrame:
        called.append(context.job_id)
        return pd.DataFrame(index=["a"], columns=["value"], data=[[1]])

    worker_module.RUNNER_REGISTRY["heavy_metric"] = _runner_entry(
        worker_module,
        metric_name="heavy_metric",
        queue="heavy",
        output=_table_output(),
        run=run,
    )
    if state != "missing":
        seed_status("job-1", "queued", client=redis)
        seed_status("job-1", state, client=redis)
    before = dict(redis.values)
    worker_module.execute_job(
        {
            "job_id": "job-1",
            "metric": "heavy_metric",
            "input": {"location": _feature_collection(), "parameters": {"value": 1}},
        },
        task_id="job-1",
    )
    assert called == []
    assert dict(redis.values) == before
