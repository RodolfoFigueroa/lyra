import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from lyra.sdk.models import FileJobResult, JobEnvelope, TableJobResult
from lyra.sdk.models.plugin_v3 import FileOutputV3, TableOutputV3

from lyra_app.config import clear_config_cache, get_config
from lyra_app.plugin_state import PluginState, make_repo_record
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from tests.config_helpers import load_test_config, plugin_state_store


def _metric(
    *,
    name: str,
    entrypoint: str,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} metric.",
        "inputs": {
            "location": {"kind": "location"},
            "value": {"kind": "integer", "required": False},
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
        "entrypoint": entrypoint,
    }


def _manifest(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": metrics,
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


def _table_output() -> TableOutputV3:
    return TableOutputV3.model_validate(
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


def _file_output() -> FileOutputV3:
    return FileOutputV3(
        kind="file",
        media_type="image/tiff",
        extensions=[".tif", ".tiff"],
    )


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}

    def set(self, key: str, value: str, *, ex: int) -> None:
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

    def xrange(
        self,
        key: str,
        *,
        min: str,  # noqa: A002
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        records = self.streams.get(key, [])
        if min.startswith("("):
            after_id = min[1:]
            records = [record for record in records if record[0] > after_id]
        return records if count is None else records[:count]


@pytest.fixture
def worker_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
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
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")


def _write_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _configure_runner_repos(
    worker: Any,
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    monkeypatch.setattr(
        worker,
        "sync_plugin_repos",
        lambda *_args, **_kwargs: [_synced_repo(repo)],
    )
    monkeypatch.setattr(worker, "install_runner_plugins", list)


def _decode_stored_result(
    worker: Any,
    redis: FakeRedisSync,
    job_id: str,
) -> dict[str, Any]:
    return json.loads(redis.values[worker.job_store.result_key(job_id)])


def _decode_status(worker: Any, redis: FakeRedisSync, job_id: str) -> dict[str, Any]:
    return json.loads(redis.values[worker.job_store.status_key(job_id)])


def test_worker_registers_only_generic_task(worker_module: Any) -> None:
    assert worker_module.GENERIC_TASK_NAME in worker_module.celery_app.tasks
    assert "light_metric" not in worker_module.celery_app.tasks
    assert "heavy_metric" not in worker_module.celery_app.tasks


def test_runner_loads_only_configured_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(
        repo,
        _manifest(
            [
                _metric(
                    name="light_metric",
                    entrypoint="missing_light_plugin:run",
                ),
                _metric(
                    name="heavy_metric",
                    entrypoint="heavy_plugin:run",
                ),
            ]
        ),
    )
    _write_module(
        tmp_path,
        "heavy_plugin",
        "def run(job, context):\n"
        "    raise AssertionError('entrypoint should only be imported')\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_repos(worker_module, monkeypatch, repo)

    entries = worker_module.refresh_runner_registry("heavy")

    assert list(entries) == ["heavy_metric"]
    assert entries["heavy_metric"].queue == "heavy"
    assert entries["heavy_metric"].entrypoint == "heavy_plugin:run"


def test_runner_syncs_enabled_state_repos_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    config = get_config()
    state = PluginState(
        repos=[
            make_repo_record("owner/enabled-plugin@main"),
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
            ["owner/enabled-plugin@main"],
            True,
        )
    ]


def test_runner_uses_configured_worker_temp_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
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
    _write_manifest(
        repo,
        _manifest(
            [
                _metric(
                    name="heavy_metric",
                    entrypoint="heavy_plugin:run",
                )
            ]
        ),
    )
    _write_module(
        tmp_path,
        "heavy_plugin",
        "def run(job, context):\n"
        "    raise AssertionError('entrypoint should only be imported')\n",
    )
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


def test_runner_fails_when_metric_queue_assignment_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    load_test_config(tmp_path, metric_queues={})
    repo = tmp_path / "repo"
    _write_manifest(
        repo,
        _manifest(
            [
                _metric(
                    name="heavy_metric",
                    entrypoint="heavy_plugin:run",
                )
            ]
        ),
    )
    _write_module(
        tmp_path,
        "heavy_plugin",
        "def run(job, context):\n"
        "    raise AssertionError('entrypoint should only be imported')\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_repos(worker_module, monkeypatch, repo)

    with pytest.raises(RuntimeError, match="no queue assignment"):
        worker_module.refresh_runner_registry("heavy")


def test_generic_task_executes_entrypoint_and_persists_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(
        repo,
        _manifest(
            [
                _metric(
                    name="heavy_metric",
                    entrypoint="success_plugin:run",
                )
            ]
        ),
    )
    _write_module(
        tmp_path,
        "success_plugin",
        "from lyra.sdk.models import TableJobResult\n"
        "def run(job, context):\n"
        "    assert context.job_id == job.job_id\n"
        "    assert context.metric == job.metric\n"
        "    assert hasattr(context, 'db')\n"
        "    context.emit_event('progress', {'percent': 50})\n"
        "    return TableJobResult(\n"
        "        job_id=job.job_id,\n"
        "        index=['area-1'],\n"
        "        columns=['value'],\n"
        "        data=[[job.input['value'] * 2]],\n"
        "    )\n",
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
    assert _decode_status(worker_module, fake_redis, "job-1")["status"] == "succeeded"
    events = worker_module.job_store.read_job_events("job-1", client=fake_redis)
    assert [event.event.event for event in events] == [
        "started",
        "progress",
        "succeeded",
    ]
    assert events[1].event.data == {"percent": 50}
    assert events[-1].event.data == payload


def test_unknown_metric_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
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
    worker_module: Any,
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
    worker_module: Any,
) -> None:
    def fail(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        msg = "boom"
        raise RuntimeError(msg)

    worker_module.RUNNER_REGISTRY["bad_metric"] = worker_module.RunnerMetricEntry(
        metric_name="bad_metric",
        queue="heavy",
        entrypoint="bad_plugin:run",
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
    worker_module: Any,
    plugin_result: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> Any:  # noqa: ARG001
        return plugin_result

    worker_module.RUNNER_REGISTRY["invalid_metric"] = worker_module.RunnerMetricEntry(
        metric_name="invalid_metric",
        queue="heavy",
        entrypoint="invalid_plugin:run",
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
    worker_module: Any,
    plugin_result: TableJobResult,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return plugin_result

    worker_module.RUNNER_REGISTRY["invalid_table_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="invalid_table_metric",
            queue="heavy",
            entrypoint="invalid_table_plugin:run",
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


def test_duplicate_resolved_location_ids_persist_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
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
            entrypoint="duplicate_location_plugin:run",
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
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> FileJobResult:
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
        entrypoint="file_plugin:run",
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
    worker_module: Any,
    filename: str,
    media_type: str,
) -> None:
    def run(job: JobEnvelope, context: Any) -> FileJobResult:
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
            entrypoint="invalid_file_plugin:run",
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
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:
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
        entrypoint="cancel_plugin:run",
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


def test_run_context_emit_event_writes_progress_event(
    worker_module: Any,
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

    context.emit_event("progress", {"percent": 50})

    events = worker_module.job_store.read_job_events("job-1", client=fake_redis)
    assert [event.event.data for event in events] == [{"percent": 50}]
    assert _decode_status(worker_module, fake_redis, "job-1")["status"] == "progress"
