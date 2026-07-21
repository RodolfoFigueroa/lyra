import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lyra.sdk.models import FailedJobResult

from lyra_app import job_store, worker_control
from lyra_app.config import clear_config_cache
from tests.config_helpers import load_test_config
from tests.redis_job_scripts import eval_job_script


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
    def __init__(
        self,
        inspector: object | None = None,
        *,
        result_state: str = "PENDING",
    ) -> None:
        self.control = FakeCeleryControl(inspector)
        self.result_state = result_state
        self.result_ids: list[str] = []

    def AsyncResult(self, task_id: str) -> object:  # noqa: N802
        self.result_ids.append(task_id)
        return type("FakeAsyncResult", (), {"state": self.result_state})()


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


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def _worker_snapshot(worker_name: str) -> worker_control.WorkerInspectSnapshot:
    return worker_control.WorkerInspectSnapshot(
        inspect_available=True,
        active={worker_name: []},
        reserved={worker_name: []},
        scheduled={worker_name: []},
        stats={worker_name: {"hostname": worker_name}},
        active_queues={worker_name: ["interactive"]},
    )


@pytest.fixture(autouse=True)
def _load_config(tmp_path: Path) -> Iterator[None]:
    worker_control.clear_worker_inspect_snapshot_cache()
    worker_control.reset_worker_inspect_collector_state()
    load_test_config(tmp_path)
    yield
    worker_control.reset_worker_inspect_collector_state()
    worker_control.clear_worker_inspect_snapshot_cache()
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
    assert [json.loads(fields["payload"])["status"] for _, fields in events] == [
        "queued",
        "failed",
    ]


def test_persist_unexpected_task_failure_uses_conditional_finalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[FailedJobResult] = []

    def save(result: FailedJobResult) -> bool:
        captured.append(result)
        return True

    monkeypatch.setattr(worker_control.job_store, "save_job_result_if_active", save)

    assert worker_control.persist_unexpected_task_failure("job-1") is True
    assert captured[0].job_id == "job-1"
    assert captured[0].error == {
        "type": "worker",
        "message": (
            "Worker execution ended unexpectedly before Lyra could persist a result."
        ),
    }


def test_reconcile_celery_failure_repairs_nonterminal_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery = FakeCeleryApp(result_state="FAILURE")
    snapshot = job_store.JobStatusSnapshot(
        job_id="job-1",
        status="running",
        updated_at=datetime.now(UTC),
        metric="heavy_metric",
    )
    repaired = snapshot.model_copy(update={"status": "failed"})
    persisted: list[str] = []

    async def get_status(_: str) -> job_store.JobStatusSnapshot:
        return repaired

    monkeypatch.setattr(worker_control, "celery_app", celery)
    monkeypatch.setattr(
        worker_control,
        "persist_unexpected_task_failure",
        lambda task_id: persisted.append(task_id) or True,
    )
    monkeypatch.setattr(worker_control.job_store, "get_job_status_async", get_status)

    result = asyncio.run(worker_control.reconcile_celery_failure(snapshot))

    assert result is repaired
    assert persisted == ["job-1"]
    assert celery.result_ids == ["job-1"]


@pytest.mark.parametrize("celery_state", ["PENDING", "STARTED", "RETRY", "SUCCESS"])
def test_reconcile_celery_failure_ignores_nonfailure_states(
    celery_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery = FakeCeleryApp(result_state=celery_state)
    snapshot = job_store.JobStatusSnapshot(
        job_id="job-1",
        status="running",
        updated_at=datetime.now(UTC),
    )
    monkeypatch.setattr(worker_control, "celery_app", celery)

    result = asyncio.run(worker_control.reconcile_celery_failure(snapshot))

    assert result is snapshot
    assert celery.result_ids == ["job-1"]


def test_reconcile_celery_failure_preserves_status_when_backend_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = job_store.JobStatusSnapshot(
        job_id="job-1",
        status="running",
        updated_at=datetime.now(UTC),
    )

    class FailingCeleryApp:
        def AsyncResult(self, _: str) -> object:  # noqa: N802
            msg = "backend unavailable"
            raise RuntimeError(msg)

    monkeypatch.setattr(worker_control, "celery_app", FailingCeleryApp())

    result = asyncio.run(worker_control.reconcile_celery_failure(snapshot))

    assert result is snapshot


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


def test_get_worker_inspect_snapshot_inspects_live_on_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _worker_snapshot("worker-1")
    calls = 0

    def inspect_workers() -> worker_control.WorkerInspectSnapshot:
        nonlocal calls
        calls += 1
        return expected

    monkeypatch.setattr(worker_control, "inspect_workers", inspect_workers)

    snapshot = worker_control.get_worker_inspect_snapshot()

    assert snapshot is expected
    assert calls == 1


def test_get_worker_inspect_snapshot_reuses_snapshot_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    snapshots = [_worker_snapshot("worker-1"), _worker_snapshot("worker-2")]
    calls = 0

    def inspect_workers() -> worker_control.WorkerInspectSnapshot:
        nonlocal calls
        snapshot = snapshots[calls]
        calls += 1
        return snapshot

    monkeypatch.setattr(worker_control.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(worker_control, "inspect_workers", inspect_workers)

    first = worker_control.get_worker_inspect_snapshot()
    second = worker_control.get_worker_inspect_snapshot()

    assert first is snapshots[0]
    assert second is first
    assert calls == 1


def test_get_worker_inspect_snapshot_refreshes_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    snapshots = [_worker_snapshot("worker-1"), _worker_snapshot("worker-2")]
    calls = 0

    def inspect_workers() -> worker_control.WorkerInspectSnapshot:
        nonlocal calls
        snapshot = snapshots[calls]
        calls += 1
        return snapshot

    monkeypatch.setattr(worker_control.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(worker_control, "inspect_workers", inspect_workers)

    first = worker_control.get_worker_inspect_snapshot()
    clock.now += worker_control.WORKER_INSPECT_CACHE_TTL_SECONDS + 0.01
    second = worker_control.get_worker_inspect_snapshot()

    assert first is snapshots[0]
    assert second is snapshots[1]
    assert calls == 2


def test_get_worker_inspect_snapshot_force_refresh_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    snapshots = [_worker_snapshot("worker-1"), _worker_snapshot("worker-2")]
    calls = 0

    def inspect_workers() -> worker_control.WorkerInspectSnapshot:
        nonlocal calls
        snapshot = snapshots[calls]
        calls += 1
        return snapshot

    monkeypatch.setattr(worker_control.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(worker_control, "inspect_workers", inspect_workers)

    first = worker_control.get_worker_inspect_snapshot()
    second = worker_control.get_worker_inspect_snapshot(force_refresh=True)

    assert first is snapshots[0]
    assert second is snapshots[1]
    assert calls == 2


def test_get_worker_inspect_snapshot_caches_unknown_state_briefly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    unknown = worker_control.WorkerInspectSnapshot(
        inspect_available=False,
        active=None,
        reserved=None,
        scheduled=None,
        stats=None,
        active_queues=None,
    )
    snapshots = [unknown, _worker_snapshot("worker-1")]
    calls = 0

    def inspect_workers() -> worker_control.WorkerInspectSnapshot:
        nonlocal calls
        snapshot = snapshots[calls]
        calls += 1
        return snapshot

    monkeypatch.setattr(worker_control.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(worker_control, "inspect_workers", inspect_workers)

    first = worker_control.get_worker_inspect_snapshot()
    second = worker_control.get_worker_inspect_snapshot()

    assert first is unknown
    assert second is unknown
    assert calls == 1


def test_get_worker_inspect_state_returns_unknown_before_background_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def inspect_workers() -> worker_control.WorkerInspectSnapshot:
        message = "unexpected live inspect"
        raise AssertionError(message)

    monkeypatch.setattr(worker_control, "inspect_workers", inspect_workers)

    state = worker_control.get_worker_inspect_state()

    assert state.snapshot.inspect_available is False
    assert state.snapshot.observed_worker_names == set()
    assert state.observed_at is None
    assert state.age_seconds is None
    assert state.stale is True


def test_refresh_worker_inspect_snapshot_updates_background_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _worker_snapshot("worker-1")

    monkeypatch.setattr(worker_control, "inspect_workers", lambda: expected)

    state = asyncio.run(worker_control.refresh_worker_inspect_snapshot())

    assert state.snapshot is expected
    assert state.observed_at is not None
    assert state.age_seconds is not None
    assert state.stale is False
    assert state.last_error is None


def test_worker_inspect_state_marks_old_snapshot_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    expected = _worker_snapshot("worker-1")

    monkeypatch.setattr(worker_control.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(worker_control, "inspect_workers", lambda: expected)

    fresh = asyncio.run(worker_control.refresh_worker_inspect_snapshot())
    clock.now += worker_control.WORKER_INSPECT_SNAPSHOT_STALE_AFTER_SECONDS + 0.01
    stale = worker_control.get_worker_inspect_state()

    assert fresh.stale is False
    assert stale.snapshot is expected
    assert stale.stale is True
    assert stale.age_seconds is not None
    assert (
        stale.age_seconds > worker_control.WORKER_INSPECT_SNAPSHOT_STALE_AFTER_SECONDS
    )


def test_refresh_worker_inspect_snapshot_records_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def inspect_workers() -> worker_control.WorkerInspectSnapshot:
        message = "inspect failed"
        raise RuntimeError(message)

    monkeypatch.setattr(worker_control, "inspect_workers", inspect_workers)

    state = asyncio.run(worker_control.refresh_worker_inspect_snapshot())

    assert state.snapshot.inspect_available is False
    assert state.observed_at is None
    assert state.stale is True
    assert state.last_error == "inspect failed"


def test_worker_inspect_collector_starts_updates_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _worker_snapshot("worker-1")
    calls = 0

    def inspect_workers() -> worker_control.WorkerInspectSnapshot:
        nonlocal calls
        calls += 1
        return expected

    async def run_collector() -> None:
        await worker_control.start_worker_inspect_collector()
        for _ in range(50):
            state = worker_control.get_worker_inspect_state()
            if state.snapshot is expected:
                break
            await asyncio.sleep(0.01)
        await worker_control.stop_worker_inspect_collector()

    monkeypatch.setattr(worker_control, "inspect_workers", inspect_workers)
    monkeypatch.setattr(
        worker_control,
        "WORKER_INSPECT_SNAPSHOT_REFRESH_INTERVAL_SECONDS",
        60.0,
    )

    asyncio.run(run_collector())

    state = worker_control.get_worker_inspect_state()
    assert calls == 1
    assert state.snapshot is expected
    assert worker_control.worker_inspect_collector_running() is False
