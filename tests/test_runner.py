from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from lyra.sdk.models import (
    FileJobResult,
    JobEnvelope,
    JobProgressEvent,
    TableJobResult,
    TerminalJobResult,
)
from lyra.sdk.models.plugin_v4 import FileOutputV4, TableOutputV4
from sqlalchemy.exc import OperationalError

from lyra_app import worker_control
from lyra_app.config import clear_config_cache, get_config
from lyra_app.db import connection as database_connection
from lyra_app.plugin_state import PluginState, make_repo_record
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from tests.config_helpers import load_test_config, plugin_state_store
from tests.redis_job_scripts import eval_job_script
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
    feature_collection,
    smoke_plugin_uri,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    from lyra.sdk.types import JsonObject

    from lyra_app.worker import WorkerRunContext


def _metric(
    *,
    name: str,
    factory: str,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} metric.",
        "inputs": {
            "location": {"kind": "location"},
            "value": {
                "kind": "integer",
                "description": "Example input value.",
            },
        },
        "output": output
        or {
            "kind": "table",
            "columns": [
                {
                    "name": "value",
                    "type": "integer",
                    "unit": "count",
                    "description": "Example output value.",
                }
            ],
        },
        "_factory": factory,
    }


def _manifest(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    factories = {metric["_factory"] for metric in metrics}
    assert len(factories) == 1
    return {
        "schema_version": 4,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "factory": next(iter(factories)),
        "metrics": [
            {key: value for key, value in metric.items() if key != "_factory"}
            for metric in metrics
        ],
    }


def _synced_repo(repo: Path) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        owner="owner",
        repo="repo",
        ref=None,
    )
    return SyncedPluginRepo(entry=entry, path=repo, changed=False)


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


def _table_output() -> TableOutputV4:
    return TableOutputV4.model_validate(
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


def _area_output(*, nullable: bool = False) -> TableOutputV4:
    return TableOutputV4.model_validate(
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


def _file_output() -> FileOutputV4:
    return FileOutputV4(
        kind="file",
        media_type="image/tiff",
        extensions=[".tif", ".tiff"],
    )


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
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

    def xadd(self, key: str, fields: dict[str, str]) -> str:
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, fields))
        return stream_id

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key: str, min: str | float, max: float) -> None:  # noqa: A002
        lower = float("-inf") if min == "-inf" else float(min)
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member, score in list(sorted_set.items()):
            if lower <= score <= max:
                sorted_set.pop(member, None)

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | float,
    ) -> int | str:
        del script
        return eval_job_script(self, numkeys, keys_and_args)

    def xrange(
        self,
        key: str,
        minimum: str,
        /,
        *,
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        records = self.streams.get(key, [])
        if minimum.startswith("("):
            after_id = minimum[1:]
            records = [record for record in records if record[0] > after_id]
        return records if count is None else records[:count]


@pytest.fixture
def worker_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ModuleType]:
    load_test_config(
        tmp_path,
        metric_queues={
            "heavy_metric": "heavy",
            "light_metric": "lightweight",
        },
    )
    worker = importlib.import_module("lyra_app.worker")
    monkeypatch.setattr(
        worker,
        "PluginStateStore",
        lambda *_args, **_kwargs: plugin_state_store(tmp_path, get_config()),
    )
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
        "    Input, LocationInput, PluginDefinition, RunContext,\n"
        "    metric as declare_metric,\n"
        ")\n"
        "from lyra.sdk.models.plugin_v4 import OutputSpecV4\n"
        "from pydantic import TypeAdapter\n"
        f"declarations = {declarations!r}\n"
        "handlers = []\n"
        "for metric_name, description, raw_output in declarations:\n"
        "    @declare_metric(\n"
        "        name=metric_name,\n"
        "        description=description,\n"
        "        inputs={'value': Input(description='Example input value.')},\n"
        "        output=TypeAdapter(OutputSpecV4).validate_python(raw_output),\n"
        "    )\n"
        "    def metric(\n"
        "        location: LocationInput, value: int, *, context: RunContext\n"
        "    ):\n"
        "        raise AssertionError('metric should only be imported')\n"
        "    handlers.append(metric)\n"
        "def create_plugin():\n"
        "    return PluginDefinition(metrics=handlers)\n",
    )


def _write_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _configure_runner_repos(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    monkeypatch.setattr(
        worker,
        "sync_plugin_repos",
        lambda *_args, **_kwargs: [_synced_repo(repo)],
    )
    monkeypatch.setattr(worker, "install_runner_plugins", list)


def _load_smoke_runner_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker: ModuleType,
) -> tuple[dict[str, Any], list[SyncedPluginRepo]]:
    load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        repos=[smoke_plugin_uri()],
    )
    installed: list[SyncedPluginRepo] = []

    def install_plugins(repos: list[SyncedPluginRepo]) -> list[SyncedPluginRepo]:
        sys.modules.pop("smoke_plugin.metrics", None)
        sys.modules.pop("smoke_plugin.plugin", None)
        sys.modules.pop("smoke_plugin", None)
        for repo in repos:
            monkeypatch.syspath_prepend(str(repo.path))
        installed.extend(repos)
        return repos

    monkeypatch.setattr(worker, "install_runner_plugins", install_plugins)
    entries = worker.refresh_runner_registry("interactive")
    return entries, installed


def _decode_stored_result(
    worker: ModuleType,
    redis: FakeRedisSync,
    job_id: str,
) -> dict[str, Any]:
    return json.loads(redis.values[worker.job_store.result_key(job_id)])


def _decode_status(
    worker: ModuleType, redis: FakeRedisSync, job_id: str
) -> dict[str, Any]:
    return json.loads(redis.values[worker.job_store.status_key(job_id)])


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

    worker_module._notify_unexpected_task_failure(task_id="job-1")  # noqa: SLF001

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
    _configure_runner_repos(worker_module, monkeypatch, repo)

    entries = worker_module.refresh_runner_registry("heavy")

    assert list(entries) == ["heavy_metric"]
    assert entries["heavy_metric"].queue == "heavy"


def test_runner_syncs_enabled_state_repos_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    config = get_config()
    directory_source = tmp_path / "directory-plugin"
    state = PluginState(
        repos=[
            make_repo_record("owner/enabled-plugin@main"),
            make_repo_record(f"dir://{directory_source}", repo_id="directory-plugin"),
            make_repo_record(
                "owner/disabled-plugin@v1.0.0",
                repo_id="disabled-plugin",
                enabled=False,
            ),
        ],
    )
    calls: list[tuple[Path, list[str], bool]] = []

    def sync_repos(
        target_dir: Path,
        raw_entries: list[str],
        *,
        raise_on_error: bool,
    ) -> list[SyncedPluginRepo]:
        calls.append((target_dir, raw_entries, raise_on_error))
        return []

    monkeypatch.setattr(worker_module, "sync_plugin_repos", sync_repos)

    synced = worker_module._runner_sync_repos("heavy", config, state)  # noqa: SLF001

    assert synced == []
    assert calls == [
        (
            tmp_path / "plugins" / "runners" / "heavy",
            [
                "owner/enabled-plugin@main",
                f"dir://{directory_source.resolve().as_posix()}",
            ],
            True,
        )
    ]


def test_runner_loads_repo_and_routing_from_plugin_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    store = plugin_state_store(tmp_path, get_config())
    store.delete_repo("owner__repo")
    store.add_repo(
        "owner/runner-plugin@main",
        repo_id="runner-plugin",
    )
    store.set_metric_queue("heavy_metric", "heavy", repo_id="runner-plugin")
    repo = tmp_path / "repo"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(repo, _manifest(metrics))
    _write_plugin_definition(tmp_path, "heavy_plugin", metrics)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(worker_module, "install_runner_plugins", list)
    calls: list[tuple[Path, list[str], bool]] = []

    def sync_repos(
        target_dir: Path,
        raw_entries: list[str],
        *,
        raise_on_error: bool,
    ) -> list[SyncedPluginRepo]:
        calls.append((target_dir, raw_entries, raise_on_error))
        return [_synced_repo(repo)]

    monkeypatch.setattr(worker_module, "sync_plugin_repos", sync_repos)

    entries = worker_module.refresh_runner_registry("heavy")

    assert calls == [
        (
            tmp_path / "plugins" / "runners" / "heavy",
            ["owner/runner-plugin@main"],
            True,
        )
    ]
    assert list(entries) == ["heavy_metric"]
    assert entries["heavy_metric"].queue == "heavy"


def test_runner_loads_directory_source_from_copied_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    source = tmp_path / "directory-plugin"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(source, _manifest(metrics))
    _write_plugin_definition(source, "heavy_plugin", metrics)
    store = plugin_state_store(tmp_path, get_config())
    store.delete_repo("owner__repo")
    store.add_repo(
        f"dir://{source}",
        repo_id="directory-plugin",
    )
    store.set_metric_queue("heavy_metric", "heavy", repo_id="directory-plugin")
    installed: list[SyncedPluginRepo] = []

    def install_plugins(repos: list[SyncedPluginRepo]) -> list[SyncedPluginRepo]:
        for repo in repos:
            monkeypatch.syspath_prepend(str(repo.path))
        installed.extend(repos)
        return repos

    monkeypatch.setattr(worker_module, "install_runner_plugins", install_plugins)

    entries = worker_module.refresh_runner_registry("heavy")

    assert len(installed) == 1
    synced = installed[0]
    assert synced.path != source
    assert synced.path.parent == tmp_path / "plugins" / "runners" / "heavy"
    assert (synced.path / MANIFEST_FILENAME).exists()
    assert list(entries) == ["heavy_metric"]
    assert entries["heavy_metric"].queue == "heavy"


def test_runner_loads_smoke_directory_fixture_from_copied_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    entries, installed = _load_smoke_runner_registry(
        tmp_path,
        monkeypatch,
        worker_module,
    )

    assert len(installed) == 1
    synced = installed[0]
    assert synced.path != SMOKE_PLUGIN_DIR
    assert synced.path.parent == tmp_path / "plugins" / "runners" / "interactive"
    assert sorted(entries) == [
        "smoke_cancel_metric",
        "smoke_file_metric",
        "smoke_table_metric",
    ]
    plugin_file = sys.modules["smoke_plugin.plugin"].__file__
    assert plugin_file is not None
    plugin_path = Path(plugin_file).resolve()
    assert plugin_path.is_relative_to(synced.path.resolve())
    assert not plugin_path.is_relative_to(SMOKE_PLUGIN_DIR.resolve())


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
    config = config.model_copy(
        update={"workers": {**config.workers, "heavy": heavy_worker}},
    )
    repo = tmp_path / "repo"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(repo, _manifest(metrics))
    _write_plugin_definition(tmp_path, "heavy_plugin", metrics)
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_repos(worker_module, monkeypatch, repo)

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


def test_runner_fails_when_metric_queue_assignment_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    load_test_config(tmp_path, metric_queues={})
    repo = tmp_path / "repo"
    _write_manifest(
        repo,
        _manifest(
            [
                _metric(
                    name="heavy_metric",
                    factory="heavy_plugin:run",
                )
            ]
        ),
    )
    _write_module(
        tmp_path,
        "heavy_plugin",
        "def run(job, context):\n"
        "    raise AssertionError('factory should only be imported')\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_repos(worker_module, monkeypatch, repo)

    with pytest.raises(RuntimeError, match="no queue assignment"):
        worker_module.refresh_runner_registry("heavy")


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
    _configure_runner_repos(worker_module, monkeypatch, repo)

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
    _configure_runner_repos(worker_module, monkeypatch, repo)

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
        "    Input, LocationInput, PluginDefinition, RunContext, metric,\n"
        ")\n"
        "from lyra.sdk.models import TableJobResult\n"
        "from lyra.sdk.models.plugin_v4 import TableOutputColumnV4, TableOutputV4\n"
        "@metric(\n"
        "    name='heavy_metric',\n"
        "    description='heavy_metric metric.',\n"
        "    inputs={'value': Input(description='Example input value.')},\n"
        "    output=TableOutputV4(\n"
        "        kind='table',\n"
        "        columns=[TableOutputColumnV4(\n"
        "            name='value', type='integer', unit='count',\n"
        "            description='Example output value.',\n"
        "        )],\n"
        "    ),\n"
        ")\n"
        "def run(location: LocationInput, value: int, *, context: RunContext):\n"
        "    assert context.metric == 'heavy_metric'\n"
        "    assert hasattr(context, 'db')\n"
        "    context.report_progress(stage='compute', current=50, total=100)\n"
        "    return TableJobResult(\n"
        "        job_id=context.job_id,\n"
        "        index=['area-1'],\n"
        "        columns=['value'],\n"
        "        data=[[value * 2]],\n"
        "    )\n"
        "def create_plugin():\n"
        "    return PluginDefinition(metrics=[run])\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_repos(worker_module, monkeypatch, repo)
    worker_module.refresh_runner_registry("heavy")
    worker_module.set_runner_temp_base(tmp_path / "tmp")

    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    payload = worker_module.execute_job(
        {
            "job_id": "job-1",
            "metric": "heavy_metric",
            "input": {"location": _feature_collection(), "value": 3},
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
    events = worker_module.job_store.read_job_events("job-1", client=fake_redis)
    assert [event.event.name for event in events] == [
        "queued",
        "running",
        "progress",
        "succeeded",
    ]
    assert isinstance(events[2].event, JobProgressEvent)
    assert events[2].event.current == 50


def test_smoke_table_metric_executes_from_directory_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    _load_smoke_runner_registry(tmp_path, monkeypatch, worker_module)
    worker_module.set_runner_temp_base(tmp_path / "tmp")
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    payload = worker_module.execute_job(
        {
            "job_id": "job-smoke-table",
            "metric": "smoke_table_metric",
            "input": {
                "location": feature_collection(("area-1", "area-2")),
                "value": 7,
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
    events = worker_module.job_store.read_job_events(
        "job-smoke-table",
        client=fake_redis,
    )
    assert [event.event.name for event in events] == [
        "queued",
        "running",
        "progress",
        "succeeded",
    ]
    assert isinstance(events[2].event, JobProgressEvent)
    assert events[2].event.stage == "table"


def test_unknown_metric_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-unknown", "metric": "missing", "input": {}},
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
    def fail(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:  # noqa: ARG001
        msg = "boom"
        raise RuntimeError(msg)

    worker_module.RUNNER_REGISTRY["bad_metric"] = worker_module.RunnerMetricEntry(
        metric_name="bad_metric",
        queue="heavy",
        output=_table_output(),
        run=fail,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-bad", "metric": "bad_metric", "input": {}},
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"] == {"type": "worker", "message": "boom"}
    assert _decode_stored_result(worker_module, fake_redis, "job-bad") == result


def test_database_exception_persists_retryable_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def fail(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:  # noqa: ARG001
        statement = "SELECT 1"
        message = "unavailable"
        raise OperationalError(statement, {}, Exception(message))

    worker_module.RUNNER_REGISTRY["database_metric"] = worker_module.RunnerMetricEntry(
        metric_name="database_metric",
        queue="heavy",
        output=_table_output(),
        run=fail,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-database", "metric": "database_metric", "input": {}},
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
        _job: JobEnvelope,
        _context: WorkerRunContext,
    ) -> TerminalJobResult | JsonObject:
        return plugin_result

    worker_module.RUNNER_REGISTRY["invalid_metric"] = worker_module.RunnerMetricEntry(
        metric_name="invalid_metric",
        queue="heavy",
        output=_table_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-invalid", "metric": "invalid_metric", "input": {}},
        task_id="task-id",
    )

    assert result["job_id"] == "job-invalid"
    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert _decode_stored_result(worker_module, fake_redis, "job-invalid") == result


@pytest.mark.parametrize(
    "plugin_result",
    [
        TableJobResult(
            job_id="job-invalid-table",
            index=["other-area"],
            columns=["value"],
            data=[[1]],
        ),
        TableJobResult(
            job_id="job-invalid-table",
            index=["area-1"],
            columns=["other_value"],
            data=[[1]],
        ),
        TableJobResult(
            job_id="job-invalid-table",
            index=["area-1"],
            columns=["value"],
            data=[["wrong"]],
        ),
        TableJobResult(
            job_id="job-invalid-table",
            index=["area-1"],
            columns=["value"],
            data=[[None]],
        ),
    ],
)
def test_invalid_table_result_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
    plugin_result: TableJobResult,
) -> None:
    def run(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:  # noqa: ARG001
        return plugin_result

    worker_module.RUNNER_REGISTRY["invalid_table_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="invalid_table_metric",
            queue="heavy",
            output=_table_output(),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-invalid-table",
            "metric": "invalid_table_metric",
            "input": {"location": _feature_collection(), "value": 1},
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
    def run(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["covered_area_m2"],
            data=[[25.0]],
        )

    worker_module.RUNNER_REGISTRY["area_metric"] = worker_module.RunnerMetricEntry(
        metric_name="area_metric",
        queue="heavy",
        output=_area_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-area",
            "metric": "area_metric",
            "input": {"location": _feature_collection()},
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
    def run(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["covered_area_m2"],
            data=[[100.00000005]],
        )

    worker_module.RUNNER_REGISTRY["area_metric"] = worker_module.RunnerMetricEntry(
        metric_name="area_metric",
        queue="heavy",
        output=_area_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-area-tolerance",
            "metric": "area_metric",
            "input": {"location": _feature_collection()},
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
    def run(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["covered_area_m2"],
            data=[[source_value]],
        )

    worker_module.RUNNER_REGISTRY["area_metric"] = worker_module.RunnerMetricEntry(
        metric_name="area_metric",
        queue="heavy",
        output=_area_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-area-range",
            "metric": "area_metric",
            "input": {"location": _feature_collection()},
            "location_areas_m2": {"area-1": 100.0},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert "outside [0, 1]" in result["error"]["message"]


def test_worker_propagates_nullable_fraction_and_requires_area_metadata(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def run(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["covered_area_m2"],
            data=[[None]],
        )

    worker_module.RUNNER_REGISTRY["area_metric"] = worker_module.RunnerMetricEntry(
        metric_name="area_metric",
        queue="heavy",
        output=_area_output(nullable=True),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    missing = worker_module.execute_job(
        {
            "job_id": "job-area-missing",
            "metric": "area_metric",
            "input": {"location": _feature_collection()},
        },
        task_id="task-id",
    )
    succeeded = worker_module.execute_job(
        {
            "job_id": "job-area-null",
            "metric": "area_metric",
            "input": {"location": _feature_collection()},
            "location_areas_m2": {"area-1": 100.0},
        },
        task_id="task-id",
    )

    assert missing["status"] == "failed"
    assert "missing server-calculated" in missing["error"]["message"]
    assert succeeded["data"] == [[None, None]]


def test_duplicate_resolved_location_ids_persist_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    def run(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["value"],
            data=[[1]],
        )

    location = _feature_collection()
    location["features"].append(location["features"][0].copy())

    worker_module.RUNNER_REGISTRY["duplicate_location_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="duplicate_location_metric",
            queue="heavy",
            output=_table_output(),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-duplicate-location",
            "metric": "duplicate_location_metric",
            "input": {"location": location, "value": 1},
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"] == {
        "type": "invalid_result",
        "message": (
            "Resolved location feature IDs must be unique after string conversion."
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
    def run(job: JobEnvelope, context: WorkerRunContext) -> FileJobResult:
        output_path = context.temp_dir / "result.tif"
        output_path.write_bytes(b"data")
        return FileJobResult(
            job_id=job.job_id,
            file_path=str(output_path),
            media_type="image/tiff",
        )

    worker_module.set_runner_temp_base(tmp_path / "tmp")
    worker_module.RUNNER_REGISTRY["file_metric"] = worker_module.RunnerMetricEntry(
        metric_name="file_metric",
        queue="heavy",
        output=_file_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-file", "metric": "file_metric", "input": {}},
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
        ("result.tif", "text/plain"),
    ],
)
def test_invalid_file_result_persists_failed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
    filename: str,
    media_type: str,
) -> None:
    def run(job: JobEnvelope, context: WorkerRunContext) -> FileJobResult:
        output_path = context.temp_dir / filename
        output_path.write_bytes(b"data")
        return FileJobResult(
            job_id=job.job_id,
            file_path=str(output_path),
            media_type=media_type,
        )

    worker_module.set_runner_temp_base(tmp_path / "tmp")
    worker_module.RUNNER_REGISTRY["invalid_file_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="invalid_file_metric",
            queue="heavy",
            output=_file_output(),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-invalid-file", "metric": "invalid_file_metric", "input": {}},
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
    def run(job: JobEnvelope, context: WorkerRunContext) -> TableJobResult:
        worker_module.job_store.set_job_status(job.job_id, "cancelled")
        context.check_cancelled()
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["value"],
            data=[[1]],
        )

    worker_module.RUNNER_REGISTRY["cancel_metric"] = worker_module.RunnerMetricEntry(
        metric_name="cancel_metric",
        queue="heavy",
        output=_table_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-cancel", "metric": "cancel_metric", "input": {}},
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
    worker_module.job_store.set_job_status(
        "job-smoke-cancel",
        "queued",
        metric="smoke_cancel_metric",
    )
    worker_module.job_store.set_job_status(
        "job-smoke-cancel",
        "cancelled",
        metric="smoke_cancel_metric",
    )

    result = worker_module.execute_job(
        {
            "job_id": "job-smoke-cancel",
            "metric": "smoke_cancel_metric",
            "input": {"location": feature_collection(), "value": 1},
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


def test_run_context_report_progress_writes_typed_event(
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

    worker_module.job_store.set_job_status(
        "job-1", "queued", metric="metric", client=fake_redis
    )
    worker_module.job_store.set_job_status(
        "job-1", "running", metric="metric", client=fake_redis
    )
    context.report_progress(stage="compute", current=50, total=100)

    events = worker_module.job_store.read_job_events("job-1", client=fake_redis)
    assert events[-1].event.name == "progress"
    assert isinstance(events[-1].event, JobProgressEvent)
    assert events[-1].event.current == 50
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
            "job_events": config.job_events.model_copy(
                update={"progress_min_interval_ms": 1_000}
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
    worker_module.job_store.set_job_status(
        "job-1", "queued", metric="metric", client=fake_redis
    )
    worker_module.job_store.set_job_status(
        "job-1", "running", metric="metric", client=fake_redis
    )

    context.report_progress(stage="compute", current=0, total=10)
    context.report_progress(stage="compute", current=1, total=10)
    context.report_progress(stage="compute", current=2, total=10)
    context.flush_events()

    events = worker_module.job_store.read_job_events("job-1", client=fake_redis)
    progress = [
        event.event.current
        for event in events
        if isinstance(event.event, JobProgressEvent)
    ]
    assert progress == [0, 2]
