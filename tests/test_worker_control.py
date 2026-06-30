import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from lyra_app import job_store, worker_control
from lyra_app.config import clear_config_cache
from tests.config_helpers import load_test_config


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations.append((key, ex))

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


class FakeCeleryControl:
    def __init__(self) -> None:
        self.revoked: list[str] = []

    def revoke(self, job_id: str) -> None:
        self.revoked.append(job_id)


class FakeCeleryApp:
    def __init__(self) -> None:
        self.control = FakeCeleryControl()


@pytest.fixture(autouse=True)
def _load_config(tmp_path: Path) -> Iterator[None]:
    load_test_config(tmp_path)
    yield
    clear_config_cache()


def test_notify_interrupted_tasks_persists_failed_job_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    monkeypatch.setattr(worker_control.job_store, "redis_client_sync", redis)

    worker_control.notify_interrupted_tasks(["job-1"])

    result = json.loads(redis.values[job_store.result_key("job-1")])
    status = json.loads(redis.values[job_store.status_key("job-1")])
    events = redis.streams[job_store.events_key("job-1")]
    assert result == {
        "kind": "failed",
        "job_id": "job-1",
        "status": "failed",
        "error": {
            "type": "worker",
            "message": (
                "This task was interrupted because plugins were updated. Please retry."
            ),
        },
    }
    assert status["status"] == "failed"
    assert len(events) == 1


def test_revoke_job_requests_celery_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery = FakeCeleryApp()
    monkeypatch.setattr(worker_control, "celery_app", celery)

    worker_control.revoke_job("job-1")

    assert celery.control.revoked == ["job-1"]
