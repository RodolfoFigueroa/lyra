import json
import logging
import time

from lyra_app.celery_app import celery_app
from lyra_app.db.redis import redis_client_sync

logger = logging.getLogger(__name__)

_INTERRUPTED_TASK_MESSAGE = (
    "This task was interrupted because plugins were updated. Please retry."
)


def notify_interrupted_tasks(task_ids: list[str]) -> None:
    for task_id in task_ids:
        payload = json.dumps(
            {
                "status": "error",
                "error_type": "worker",
                "message": _INTERRUPTED_TASK_MESSAGE,
            }
        )
        redis_client_sync.publish(f"task_results_{task_id}", payload)
        logger.info("Notified task %s of interruption.", task_id)


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
