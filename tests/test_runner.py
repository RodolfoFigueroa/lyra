import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo


def _manifest() -> dict[str, Any]:
    metric = {
        "description": "A metric.",
        "parameters": [
            {"name": "value", "type": "int", "required": True, "default": None}
        ],
        "returns_file": False,
        "tavi_hint": "",
        "request_schema": {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer"}},
        },
        "callable": {"mode": "single", "calculate": "fake_plugin:calculate"},
    }
    return {
        "schema_version": 1,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [
            {
                **metric,
                "name": "light_metric",
                "execution": {
                    "profile": "lightweight",
                    "queue": "lightweight",
                    "timeout_seconds": 30,
                },
            },
            {
                **metric,
                "name": "heavy_metric",
                "execution": {
                    "profile": "heavy",
                    "queue": "heavy",
                    "timeout_seconds": 120,
                },
            },
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


class FakeRedisSync:
    def __init__(self) -> None:
        self.setex_calls: list[tuple[str, int, str]] = []
        self.published: list[tuple[str, str]] = []

    def get(self, key: str) -> None:  # noqa: ARG002
        return None

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))

    def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


class FakeRequest:
    id = "task-id"


class FakeTask:
    request = FakeRequest()
    name = "heavy_metric"


def test_runner_loads_only_configured_queue_and_executes_metric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LYRA_PLUGIN_REPOS", raising=False)
    worker = importlib.import_module("lyra_app.worker")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")
    (tmp_path / "fake_plugin.py").write_text(
        "def calculate(value: int) -> dict:\n    return {'value': value * 2}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("LYRA_RUNNER_QUEUES", "heavy")
    monkeypatch.setattr(worker, "sync_runner_repos", lambda: [_synced_repo(repo)])
    monkeypatch.setattr(worker, "install_runner_plugins", list)

    entries = worker.load_runner_metric_entries()

    assert list(entries) == ["heavy_metric"]

    entry = entries["heavy_metric"]
    assert entry.calculate is not None
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker, "redis_client_sync", fake_redis)
    wrapper = worker.make_celery_wrapper(
        entry.calculate,
        entry.model,
        entry.params_to_convert,
        entry.db_param_name,
    )

    notification = wrapper(FakeTask(), {"value": 3})

    assert notification == {"status": "success", "download_id": "task-id"}
    stored_payload = json.loads(fake_redis.setex_calls[0][2])
    assert stored_payload == {"status": "success", "result": {"value": 6}}
