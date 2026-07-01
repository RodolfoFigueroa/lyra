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
    def __init__(self, inspector: object | None = None) -> None:
        self.revoked: list[str] = []
        self.inspect_kwargs: list[dict[str, object]] = []
        self.inspector = inspector

    def revoke(self, job_id: str) -> None:
        self.revoked.append(job_id)

    def inspect(self, **kwargs: object) -> object:
        self.inspect_kwargs.append(kwargs)
        assert self.inspector is not None
        return self.inspector


class FakeCeleryApp:
    def __init__(self, inspector: object | None = None) -> None:
        self.control = FakeCeleryControl(inspector)


class FakeInspector:
    def __init__(
        self,
        *,
        active: dict[str, list[dict[str, object]]] | None = None,
        reserved: dict[str, list[dict[str, object]]] | None = None,
        scheduled: dict[str, list[dict[str, object]]] | None = None,
        stats: dict[str, dict[str, object]] | None = None,
        active_queues: dict[str, list[dict[str, object]]] | None = None,
    ) -> None:
        self._active = active
        self._reserved = reserved
        self._scheduled = scheduled
        self._stats = stats
        self._active_queues = active_queues

    def active(self) -> dict[str, list[dict[str, object]]] | None:
        return self._active

    def reserved(self) -> dict[str, list[dict[str, object]]] | None:
        return self._reserved

    def scheduled(self) -> dict[str, list[dict[str, object]]] | None:
        return self._scheduled

    def stats(self) -> dict[str, dict[str, object]] | None:
        return self._stats

    def active_queues(self) -> dict[str, list[dict[str, object]]] | None:
        return self._active_queues


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


def test_inspect_workers_normalizes_celery_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery = FakeCeleryApp(
        FakeInspector(
            active={"worker-1": [{"id": "job-1", "name": "lyra.run_metric"}]},
            reserved={"worker-1": []},
            scheduled={
                "worker-1": [
                    {
                        "eta": "2026-01-01T00:00:00Z",
                        "request": {"id": "job-2", "name": "lyra.run_metric"},
                    }
                ]
            },
            stats={"worker-1": {"hostname": "worker-1"}},
            active_queues={"worker-1": [{"name": "interactive"}]},
        )
    )
    monkeypatch.setattr(worker_control, "celery_app", celery)

    snapshot = worker_control.inspect_workers()
    assert snapshot.scheduled is not None
    task = worker_control.safe_task_summary(
        snapshot.scheduled["worker-1"][0],
        worker_name="worker-1",
    )

    assert snapshot.inspect_available is True
    assert snapshot.observed_worker_names == {"worker-1"}
    assert snapshot.active_queues == {"worker-1": ["interactive"]}
    assert task == {
        "id": "job-2",
        "name": "lyra.run_metric",
        "worker": "worker-1",
        "eta": "2026-01-01T00:00:00Z",
        "time_start": None,
    }


def test_inspect_workers_treats_missing_celery_data_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery = FakeCeleryApp(FakeInspector())
    monkeypatch.setattr(worker_control, "celery_app", celery)

    snapshot = worker_control.inspect_workers()

    assert snapshot.inspect_available is False
    assert snapshot.observed_worker_names == set()


def test_inspect_workers_uses_explicit_short_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery = FakeCeleryApp(FakeInspector())
    monkeypatch.setattr(worker_control, "celery_app", celery)

    worker_control.inspect_workers()

    assert celery.control.inspect_kwargs == [
        {"timeout": worker_control.DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS}
    ]
    assert 0 < worker_control.DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS < 1.0
