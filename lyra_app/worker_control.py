import logging
import time
from dataclasses import dataclass
from typing import Any

from lyra.sdk.models import FailedJobResult

from lyra_app import job_store
from lyra_app.celery_app import celery_app

logger = logging.getLogger(__name__)

_INTERRUPTED_TASK_MESSAGE = (
    "This task was interrupted because plugins were updated. Please retry."
)
DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True)
class WorkerInspectSnapshot:
    inspect_available: bool
    active: dict[str, list[dict[str, Any]]] | None
    reserved: dict[str, list[dict[str, Any]]] | None
    scheduled: dict[str, list[dict[str, Any]]] | None
    stats: dict[str, dict[str, Any]] | None
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


def _inspect_call(inspector: Any, method_name: str) -> Any | None:
    try:
        return getattr(inspector, method_name)()
    except Exception:  # noqa: BLE001  # pragma: no cover - Celery transports vary
        logger.warning("Celery inspect.%s() failed.", method_name, exc_info=True)
        return None


def _normalise_task_section(raw: Any | None) -> dict[str, list[dict[str, Any]]] | None:
    if raw is None or not isinstance(raw, dict):
        return None
    normalised: dict[str, list[dict[str, Any]]] = {}
    for worker_name, tasks in raw.items():
        if not isinstance(worker_name, str):
            continue
        if isinstance(tasks, list):
            normalised[worker_name] = [task for task in tasks if isinstance(task, dict)]
        else:
            normalised[worker_name] = []
    return normalised


def _normalise_stats(raw: Any | None) -> dict[str, dict[str, Any]] | None:
    if raw is None or not isinstance(raw, dict):
        return None
    return {
        worker_name: stats
        for worker_name, stats in raw.items()
        if isinstance(worker_name, str) and isinstance(stats, dict)
    }


def _normalise_active_queues(raw: Any | None) -> dict[str, list[str]] | None:
    if raw is None or not isinstance(raw, dict):
        return None
    queues_by_worker: dict[str, list[str]] = {}
    for worker_name, queues in raw.items():
        if not isinstance(worker_name, str):
            continue
        queue_names: list[str] = []
        if isinstance(queues, list):
            queue_names.extend(
                queue["name"]
                for queue in queues
                if isinstance(queue, dict) and isinstance(queue.get("name"), str)
            )
        queues_by_worker[worker_name] = sorted(set(queue_names))
    return queues_by_worker


def inspect_workers() -> WorkerInspectSnapshot:
    inspector = celery_app.control.inspect(
        timeout=DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS
    )
    active = _normalise_task_section(_inspect_call(inspector, "active"))
    reserved = _normalise_task_section(_inspect_call(inspector, "reserved"))
    scheduled = _normalise_task_section(_inspect_call(inspector, "scheduled"))
    stats = _normalise_stats(_inspect_call(inspector, "stats"))
    active_queues = _normalise_active_queues(_inspect_call(inspector, "active_queues"))
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


def safe_task_summary(task: dict[str, Any], *, worker_name: str) -> dict[str, Any]:
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
