import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from celery import states
from lyra.sdk.models import FailedJobResult
from lyra.sdk.types import JsonObject, JsonValue

from lyra_app import job_store
from lyra_app.celery_app import celery_app

logger = logging.getLogger(__name__)

_INTERRUPTED_TASK_MESSAGE = (
    "This task was interrupted because plugins were updated. Please retry."
)
_UNEXPECTED_TASK_FAILURE_MESSAGE = (
    "Worker execution ended unexpectedly before Lyra could persist a result."
)
DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS = 0.5
WORKER_INSPECT_CACHE_TTL_SECONDS = 1.0
WORKER_INSPECT_SNAPSHOT_REFRESH_INTERVAL_SECONDS = 2.0
WORKER_INSPECT_SNAPSHOT_STALE_AFTER_SECONDS = 10.0


@dataclass(frozen=True)
class WorkerInspectSnapshot:
    inspect_available: bool
    active: dict[str, list[JsonObject]] | None
    reserved: dict[str, list[JsonObject]] | None
    scheduled: dict[str, list[JsonObject]] | None
    stats: dict[str, JsonObject] | None
    active_queues: dict[str, list[str]] | None

    @property
    def observed_worker_names(self) -> set[str]:
        names: set[str] = set()
        for section in (
            self.active,
            self.reserved,
            self.scheduled,
            self.stats,
            self.active_queues,
        ):
            if section is not None:
                names.update(section)
        return names


@dataclass(frozen=True)
class WorkerInspectState:
    snapshot: WorkerInspectSnapshot
    observed_at: datetime | None
    age_seconds: float | None
    stale: bool
    last_error: str | None


@dataclass
class _WorkerInspectCache:
    snapshot: WorkerInspectSnapshot | None = None
    observed_at: float | None = None


@dataclass
class _WorkerInspectCollectorState:
    snapshot: WorkerInspectSnapshot | None = None
    observed_at: datetime | None = None
    observed_at_monotonic: float | None = None
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    task: asyncio.Task[None] | None = None
    stop_event: asyncio.Event | None = None


_WORKER_INSPECT_CACHE = _WorkerInspectCache()
_WORKER_INSPECT_COLLECTOR = _WorkerInspectCollectorState()
_WORKER_INSPECT_COLLECTOR_LOCK = Lock()

_UNKNOWN_WORKER_INSPECT_SNAPSHOT = WorkerInspectSnapshot(
    inspect_available=False,
    active=None,
    reserved=None,
    scheduled=None,
    stats=None,
    active_queues=None,
)


def _inspect_call(call: Callable[[], JsonValue], method_name: str) -> JsonValue:
    try:
        return call()
    except Exception:  # noqa: BLE001  # pragma: no cover - Celery transports vary
        logger.warning("Celery inspect.%s() failed.", method_name, exc_info=True)
        return None


def _normalise_task_section(raw: JsonValue) -> dict[str, list[JsonObject]] | None:
    if raw is None or not isinstance(raw, dict):
        return None
    normalised: dict[str, list[JsonObject]] = {}
    for worker_name, tasks in raw.items():
        if not isinstance(worker_name, str):
            continue
        if isinstance(tasks, list):
            normalised[worker_name] = [task for task in tasks if isinstance(task, dict)]
        else:
            normalised[worker_name] = []
    return normalised


def _normalise_stats(raw: JsonValue) -> dict[str, JsonObject] | None:
    if raw is None or not isinstance(raw, dict):
        return None
    return {
        worker_name: stats
        for worker_name, stats in raw.items()
        if isinstance(worker_name, str) and isinstance(stats, dict)
    }


def _normalise_active_queues(raw: JsonValue) -> dict[str, list[str]] | None:
    if raw is None or not isinstance(raw, dict):
        return None
    queues_by_worker: dict[str, list[str]] = {}
    for worker_name, queues in raw.items():
        if not isinstance(worker_name, str):
            continue
        queue_names: list[str] = []
        if isinstance(queues, list):
            for queue in queues:
                if not isinstance(queue, dict):
                    continue
                name = queue.get("name")
                if isinstance(name, str):
                    queue_names.append(name)
        queues_by_worker[worker_name] = sorted(set(queue_names))
    return queues_by_worker


def inspect_workers() -> WorkerInspectSnapshot:
    inspector = celery_app.control.inspect(
        timeout=DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS
    )
    active = _normalise_task_section(_inspect_call(inspector.active, "active"))
    reserved = _normalise_task_section(_inspect_call(inspector.reserved, "reserved"))
    scheduled = _normalise_task_section(_inspect_call(inspector.scheduled, "scheduled"))
    stats = _normalise_stats(_inspect_call(inspector.stats, "stats"))
    active_queues = _normalise_active_queues(
        _inspect_call(inspector.active_queues, "active_queues")
    )
    return WorkerInspectSnapshot(
        inspect_available=any(
            section is not None
            for section in (active, reserved, scheduled, stats, active_queues)
        ),
        active=active,
        reserved=reserved,
        scheduled=scheduled,
        stats=stats,
        active_queues=active_queues,
    )


def clear_worker_inspect_snapshot_cache() -> None:
    _WORKER_INSPECT_CACHE.snapshot = None
    _WORKER_INSPECT_CACHE.observed_at = None


def get_worker_inspect_snapshot(
    *,
    force_refresh: bool = False,
) -> WorkerInspectSnapshot:
    now = time.monotonic()
    if (
        not force_refresh
        and _WORKER_INSPECT_CACHE.snapshot is not None
        and _WORKER_INSPECT_CACHE.observed_at is not None
        and now - _WORKER_INSPECT_CACHE.observed_at < WORKER_INSPECT_CACHE_TTL_SECONDS
    ):
        return _WORKER_INSPECT_CACHE.snapshot

    snapshot = inspect_workers()
    _WORKER_INSPECT_CACHE.snapshot = snapshot
    _WORKER_INSPECT_CACHE.observed_at = time.monotonic()
    return snapshot


def reset_worker_inspect_collector_state() -> None:
    with _WORKER_INSPECT_COLLECTOR_LOCK:
        _WORKER_INSPECT_COLLECTOR.snapshot = None
        _WORKER_INSPECT_COLLECTOR.observed_at = None
        _WORKER_INSPECT_COLLECTOR.observed_at_monotonic = None
        _WORKER_INSPECT_COLLECTOR.last_attempt_at = None
        _WORKER_INSPECT_COLLECTOR.last_error = None


def get_worker_inspect_state() -> WorkerInspectState:
    now = time.monotonic()
    with _WORKER_INSPECT_COLLECTOR_LOCK:
        snapshot = _WORKER_INSPECT_COLLECTOR.snapshot
        observed_at = _WORKER_INSPECT_COLLECTOR.observed_at
        observed_at_monotonic = _WORKER_INSPECT_COLLECTOR.observed_at_monotonic
        last_error = _WORKER_INSPECT_COLLECTOR.last_error

    if snapshot is None or observed_at_monotonic is None:
        return WorkerInspectState(
            snapshot=_UNKNOWN_WORKER_INSPECT_SNAPSHOT,
            observed_at=None,
            age_seconds=None,
            stale=True,
            last_error=last_error,
        )

    age_seconds = max(0.0, now - observed_at_monotonic)
    return WorkerInspectState(
        snapshot=snapshot,
        observed_at=observed_at,
        age_seconds=age_seconds,
        stale=age_seconds > WORKER_INSPECT_SNAPSHOT_STALE_AFTER_SECONDS,
        last_error=last_error,
    )


async def refresh_worker_inspect_snapshot() -> WorkerInspectState:
    attempt_at = datetime.now(UTC)
    try:
        snapshot = await asyncio.to_thread(inspect_workers)
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - transport setup varies
        logger.warning("Background worker inspect refresh failed.", exc_info=True)
        with _WORKER_INSPECT_COLLECTOR_LOCK:
            _WORKER_INSPECT_COLLECTOR.last_attempt_at = attempt_at
            _WORKER_INSPECT_COLLECTOR.last_error = str(exc) or type(exc).__name__
        return get_worker_inspect_state()

    observed_at = datetime.now(UTC)
    with _WORKER_INSPECT_COLLECTOR_LOCK:
        _WORKER_INSPECT_COLLECTOR.snapshot = snapshot
        _WORKER_INSPECT_COLLECTOR.observed_at = observed_at
        _WORKER_INSPECT_COLLECTOR.observed_at_monotonic = time.monotonic()
        _WORKER_INSPECT_COLLECTOR.last_attempt_at = attempt_at
        _WORKER_INSPECT_COLLECTOR.last_error = None
    return get_worker_inspect_state()


async def _worker_inspect_collector_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await refresh_worker_inspect_snapshot()
        with suppress(TimeoutError):
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=WORKER_INSPECT_SNAPSHOT_REFRESH_INTERVAL_SECONDS,
            )


async def start_worker_inspect_collector() -> None:
    with _WORKER_INSPECT_COLLECTOR_LOCK:
        task = _WORKER_INSPECT_COLLECTOR.task
        if task is not None and not task.done():
            return
        stop_event = asyncio.Event()
        _WORKER_INSPECT_COLLECTOR.stop_event = stop_event
        _WORKER_INSPECT_COLLECTOR.task = asyncio.create_task(
            _worker_inspect_collector_loop(stop_event),
            name="lyra-worker-inspect-collector",
        )


async def stop_worker_inspect_collector() -> None:
    with _WORKER_INSPECT_COLLECTOR_LOCK:
        task = _WORKER_INSPECT_COLLECTOR.task
        stop_event = _WORKER_INSPECT_COLLECTOR.stop_event

    if task is None:
        return

    if stop_event is not None:
        stop_event.set()
    with suppress(asyncio.CancelledError):
        await task

    with _WORKER_INSPECT_COLLECTOR_LOCK:
        if _WORKER_INSPECT_COLLECTOR.task is task:
            _WORKER_INSPECT_COLLECTOR.task = None
            _WORKER_INSPECT_COLLECTOR.stop_event = None


def worker_inspect_collector_running() -> bool:
    with _WORKER_INSPECT_COLLECTOR_LOCK:
        task = _WORKER_INSPECT_COLLECTOR.task
    return task is not None and not task.done()


def safe_task_summary(task: JsonObject, *, worker_name: str) -> JsonObject:
    raw_request = task.get("request")
    request = raw_request if isinstance(raw_request, dict) else task
    time_start = request.get("time_start") or task.get("time_start")
    return {
        "id": request.get("id") if isinstance(request.get("id"), str) else None,
        "name": request.get("name") if isinstance(request.get("name"), str) else None,
        "worker": worker_name,
        "eta": (
            task.get("eta")
            if isinstance(task.get("eta"), str)
            else request.get("eta")
            if isinstance(request.get("eta"), str)
            else None
        ),
        "time_start": time_start if isinstance(time_start, int | float) else None,
    }


def notify_interrupted_tasks(task_ids: list[str]) -> None:
    for task_id in task_ids:
        job_store.save_job_result(
            FailedJobResult(
                job_id=task_id,
                error={
                    "type": "worker",
                    "message": _INTERRUPTED_TASK_MESSAGE,
                },
            )
        )
        logger.info("Notified task %s of interruption.", task_id)


def persist_unexpected_task_failure(task_id: str) -> bool:
    """Persist a Celery-level failure without replacing a terminal Lyra job."""
    return job_store.save_job_result_if_active(
        FailedJobResult(
            job_id=task_id,
            error={
                "type": "worker",
                "message": _UNEXPECTED_TASK_FAILURE_MESSAGE,
            },
        )
    )


def notify_unexpected_task_failure(task_id: str) -> None:
    """Best-effort signal receiver entry point for unexpected task failures."""
    try:
        saved = persist_unexpected_task_failure(task_id)
    except Exception:  # Celery signal handlers must not escape
        logger.exception("Could not persist unexpected failure for task %s.", task_id)
        return
    if saved:
        logger.info("Persisted unexpected failure for task %s.", task_id)


async def reconcile_celery_failure(
    snapshot: job_store.JobStatusSnapshot,
) -> job_store.JobStatusSnapshot:
    """Repair a nonterminal Lyra job when Celery has a terminal failure."""
    if job_store.is_terminal_status(snapshot.status):
        return snapshot

    try:
        celery_state = await asyncio.to_thread(
            lambda: celery_app.AsyncResult(snapshot.job_id).state
        )
    except Exception:  # noqa: BLE001  # Result backends fail independently
        logger.warning(
            "Could not reconcile Celery state for task %s.",
            snapshot.job_id,
            exc_info=True,
        )
        return snapshot

    if celery_state != states.FAILURE:
        return snapshot

    try:
        saved = await asyncio.to_thread(
            persist_unexpected_task_failure,
            snapshot.job_id,
        )
        repaired = await job_store.get_job_status_async(snapshot.job_id)
    except Exception:  # noqa: BLE001  # Preserve the last readable Lyra state
        logger.warning(
            "Could not persist reconciled failure for task %s.",
            snapshot.job_id,
            exc_info=True,
        )
        return snapshot

    if saved:
        logger.info("Reconciled Celery failure for task %s.", snapshot.job_id)
    return repaired or snapshot


def revoke_job(job_id: str) -> None:
    celery_app.control.revoke(job_id)
    logger.info("Requested cancellation for task %s.", job_id)


def graceful_worker_restart(timeout: float = 30.0) -> None:
    inspector = celery_app.control.inspect()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        active = inspector.active()
        if not active or all(len(tasks) == 0 for tasks in active.values()):
            logger.info("All workers idle; issuing graceful shutdown.")
            celery_app.control.broadcast("shutdown")
            return
        time.sleep(1)

    active = inspector.active() or {}
    interrupted_ids: list[str] = [
        task["id"] for tasks in active.values() for task in tasks
    ]

    if interrupted_ids:
        logger.warning(
            "Timeout exceeded with %d task(s) still running; terminating.",
            len(interrupted_ids),
        )
        notify_interrupted_tasks(interrupted_ids)
        for task_id in interrupted_ids:
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

    celery_app.control.broadcast("shutdown")
