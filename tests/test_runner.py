import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from lyra.sdk.models import JobEnvelope, JobResult

from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo


def _metric(
    *,
    name: str,
    queue: str,
    entrypoint: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} metric.",
        "request_schema": {
            "type": "object",
            "required": ["location"],
            "properties": {"location": {}, "value": {"type": "integer"}},
        },
        "spatial_inputs": {"location": "location"},
        "execution": {"queue": queue},
        "entrypoint": entrypoint,
    }


def _manifest(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 2,
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
def worker_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("LYRA_PLUGIN_REPOS", raising=False)
    worker = importlib.import_module("lyra_app.worker")
    worker.RUNNER_REGISTRY.clear()
    yield worker
    worker.RUNNER_REGISTRY.clear()


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
    monkeypatch.setattr(worker, "sync_runner_repos", lambda: [_synced_repo(repo)])
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
                    queue="lightweight",
                    entrypoint="missing_light_plugin:run",
                ),
                _metric(
                    name="heavy_metric",
                    queue="heavy",
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
    monkeypatch.setenv("LYRA_RUNNER_QUEUES", "heavy")
    _configure_runner_repos(worker_module, monkeypatch, repo)

    entries = worker_module.refresh_runner_registry()

    assert list(entries) == ["heavy_metric"]
    assert entries["heavy_metric"].queue == "heavy"
    assert entries["heavy_metric"].entrypoint == "heavy_plugin:run"


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
                    queue="heavy",
                    entrypoint="success_plugin:run",
                )
            ]
        ),
    )
    _write_module(
        tmp_path,
        "success_plugin",
        "from lyra.sdk.models import JobResult\n"
        "def run(job, context):\n"
        "    assert context.job_id == job.job_id\n"
        "    assert context.metric == job.metric\n"
        "    assert hasattr(context, 'db')\n"
        "    context.emit_event('progress', {'percent': 50})\n"
        "    return JobResult(\n"
        "        job_id=job.job_id,\n"
        "        status='succeeded',\n"
        "        result={\n"
        "            'value': job.input['value'] * 2,\n"
        "            'temp': context.temp_dir.name,\n"
        "        },\n"
        "    )\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("LYRA_RUNNER_QUEUES", "heavy")
    monkeypatch.setenv("LYRA_RUNNER_TEMP_DIR", str(tmp_path / "tmp"))
    _configure_runner_repos(worker_module, monkeypatch, repo)
    worker_module.refresh_runner_registry()

    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    payload = worker_module.execute_job(
        {"job_id": "job-1", "metric": "heavy_metric", "input": {"value": 3}},
        task_id="task-id",
    )

    assert payload == {
        "job_id": "job-1",
        "status": "succeeded",
        "result": {"value": 6, "temp": "job-1"},
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
    def fail(job: JobEnvelope, context: Any) -> JobResult:  # noqa: ARG001
        msg = "boom"
        raise RuntimeError(msg)

    worker_module.RUNNER_REGISTRY["bad_metric"] = worker_module.RunnerMetricEntryV2(
        metric_name="bad_metric",
        queue="heavy",
        entrypoint="bad_plugin:run",
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
        JobResult(job_id="other-job", status="succeeded", result={"value": 1}),
    ],
)
def test_invalid_plugin_result_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
    plugin_result: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> Any:  # noqa: ARG001
        return plugin_result

    worker_module.RUNNER_REGISTRY["invalid_metric"] = worker_module.RunnerMetricEntryV2(
        metric_name="invalid_metric",
        queue="heavy",
        entrypoint="invalid_plugin:run",
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


def test_file_result_persists_through_generic_result_path(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> JobResult:  # noqa: ARG001
        return JobResult(
            job_id=job.job_id,
            status="succeeded",
            result_type="file",
            file_path="result.tif",
        )

    worker_module.RUNNER_REGISTRY["file_metric"] = worker_module.RunnerMetricEntryV2(
        metric_name="file_metric",
        queue="heavy",
        entrypoint="file_plugin:run",
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-file", "metric": "file_metric", "input": {}},
        task_id="task-id",
    )

    assert result == {
        "job_id": "job-file",
        "status": "succeeded",
        "result_type": "file",
        "file_path": "result.tif",
    }
    assert _decode_stored_result(worker_module, fake_redis, "job-file") == result


def test_check_cancelled_persists_cancelled_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> JobResult:
        worker_module.job_store.set_job_status(job.job_id, "cancelled")
        context.check_cancelled()
        return JobResult(job_id=job.job_id, status="succeeded", result={"done": True})

    worker_module.RUNNER_REGISTRY["cancel_metric"] = worker_module.RunnerMetricEntryV2(
        metric_name="cancel_metric",
        queue="heavy",
        entrypoint="cancel_plugin:run",
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {"job_id": "job-cancel", "metric": "cancel_metric", "input": {}},
        task_id="task-id",
    )

    assert result == {"job_id": "job-cancel", "status": "cancelled"}
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
