import json

import pytest

from lyra_app import job_store, worker_control


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations.append((key, ex))

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))


def test_notify_interrupted_tasks_persists_failed_job_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    monkeypatch.setattr(worker_control.job_store, "redis_client_sync", redis)

    worker_control.notify_interrupted_tasks(["job-1"])

    result = json.loads(redis.values[job_store.result_key("job-1")])
    status = json.loads(redis.values[job_store.status_key("job-1")])
    assert result == {
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
