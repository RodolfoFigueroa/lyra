import importlib
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from celery import Task
from lyra.sdk.models import JobEnvelope, JobResult
from lyra.sdk.models.plugin_v2 import MetricManifestV2
from pydantic import ValidationError as PydanticValidationError

from lyra_app import job_store
from lyra_app.celery_app import celery_app
from lyra_app.plugins import install_runner_plugins, sync_runner_repos
from lyra_app.registry import load_plugin_manifest

logger = logging.getLogger(__name__)

GENERIC_TASK_NAME = "lyra.run_metric"

MetricRunCallable = Callable[
    [JobEnvelope, "WorkerRunContext"], JobResult | dict[str, Any]
]


@dataclass(frozen=True)
class RunnerMetricEntryV2:
    metric_name: str
    queue: str
    entrypoint: str
    run: MetricRunCallable


@dataclass(frozen=True)
class WorkerRunContext:
    job_id: str
    metric: str
    logger: logging.Logger
    temp_dir: Path
    db: Any | None

    def emit_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        job_store.append_job_event(
            self.job_id,
            event,
            data,
            metric=self.metric,
        )

    def check_cancelled(self) -> None:
        job_store.raise_if_cancelled(self.job_id)


RUNNER_REGISTRY: dict[str, RunnerMetricEntryV2] = {}


def _configured_runner_queues() -> set[str]:
    raw = os.environ.get("LYRA_RUNNER_QUEUES", "").strip()
    if not raw:
        return set()
    return {queue.strip() for queue in raw.split(",") if queue.strip()}


def _load_entrypoint(spec: str) -> MetricRunCallable:
    module_name, sep, function_name = spec.partition(":")
    if not sep or not module_name or not function_name:
        msg = f"Entrypoint must use 'module:function' format: {spec!r}"
        raise ValueError(msg)

    value = getattr(importlib.import_module(module_name), function_name)
    if not callable(value):
        msg = f"Entrypoint {spec!r} did not resolve to a callable."
        raise TypeError(msg)
    return value


def _entry_from_metric(metric: MetricManifestV2) -> RunnerMetricEntryV2:
    return RunnerMetricEntryV2(
        metric_name=metric.name,
        queue=metric.execution.queue,
        entrypoint=metric.entrypoint,
        run=_load_entrypoint(metric.entrypoint),
    )


def load_runner_metric_entries() -> dict[str, RunnerMetricEntryV2]:
    queues = _configured_runner_queues()
    repos = install_runner_plugins(sync_runner_repos())
    entries: dict[str, RunnerMetricEntryV2] = {}

    for repo in repos:
        manifest = load_plugin_manifest(repo.path)
        for metric in manifest.metrics:
            if queues and metric.execution.queue not in queues:
                continue
            if metric.name in entries:
                msg = f"Duplicate metric name in runner manifests: {metric.name!r}"
                raise RuntimeError(msg)
            entries[metric.name] = _entry_from_metric(metric)

    return entries


def refresh_runner_registry() -> dict[str, RunnerMetricEntryV2]:
    registry = load_runner_metric_entries()
    RUNNER_REGISTRY.clear()
    RUNNER_REGISTRY.update(registry)
    logger.info(
        "Loaded %d v2 runner metric(s) for generic task %s.",
        len(RUNNER_REGISTRY),
        GENERIC_TASK_NAME,
    )
    return dict(RUNNER_REGISTRY)


def _runner_temp_base() -> Path:
    configured = os.environ.get("LYRA_RUNNER_TEMP_DIR") or os.environ.get(
        "LYRA_CACHE_DIR"
    )
    if configured:
        return Path(configured)

    cache_dir = Path("/lyra_cache")
    if cache_dir.exists():
        return cache_dir

    return Path(tempfile.gettempdir()) / "lyra"


def _safe_path_segment(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "_"
        for character in value
    )


def build_run_context(job: JobEnvelope) -> WorkerRunContext:
    temp_dir = _runner_temp_base() / "jobs" / _safe_path_segment(job.job_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return WorkerRunContext(
        job_id=job.job_id,
        metric=job.metric,
        logger=logging.getLogger(f"{__name__}.{job.metric}"),
        temp_dir=temp_dir,
        db=_build_db_context(),
    )


def _build_db_context() -> Any | None:
    try:
        from lyra_app.db.client import LyraDBImplicit  # noqa: PLC0415
    except KeyError as exc:
        logger.info("DB context unavailable: missing environment variable %s.", exc)
        return None
    return LyraDBImplicit()


def _job_id_from_payload(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        job_id = payload.get("job_id")
        if isinstance(job_id, str) and job_id:
            return job_id
    return fallback


def _failed_result(job_id: str, error_type: str, message: str) -> JobResult:
    return JobResult(
        job_id=job_id,
        status="failed",
        error={"type": error_type, "message": message},
    )


def _cancelled_result(job_id: str) -> JobResult:
    return JobResult(job_id=job_id, status="cancelled")


def _persist_result(result: JobResult, *, metric: str | None = None) -> dict[str, Any]:
    return job_store.save_job_result(result, metric=metric)


def _normalise_plugin_result(raw_result: Any, job: JobEnvelope) -> JobResult:
    try:
        result = (
            raw_result
            if isinstance(raw_result, JobResult)
            else JobResult.model_validate(raw_result)
        )
    except PydanticValidationError as exc:
        return _failed_result(job.job_id, "invalid_result", str(exc))

    if result.job_id != job.job_id:
        return _failed_result(
            job.job_id,
            "invalid_result",
            f"Plugin returned job_id {result.job_id!r} for job {job.job_id!r}.",
        )
    return result


def execute_job(envelope_payload: Any, *, task_id: str) -> dict[str, Any]:
    fallback_job_id = _job_id_from_payload(envelope_payload, task_id)
    try:
        job = JobEnvelope.model_validate(envelope_payload)
    except PydanticValidationError as exc:
        return _persist_result(
            _failed_result(fallback_job_id, "invalid_envelope", str(exc))
        )

    entry = RUNNER_REGISTRY.get(job.metric)
    if entry is None:
        return _persist_result(
            _failed_result(
                job.job_id,
                "unknown_metric",
                f"Unknown metric: {job.metric}",
            ),
            metric=job.metric,
        )

    if job_store.is_job_cancelled(job.job_id):
        return _persist_result(_cancelled_result(job.job_id), metric=job.metric)

    job_store.set_job_status(job.job_id, "started", metric=job.metric)

    try:
        context = build_run_context(job)
        raw_result = entry.run(job, context)
    except job_store.JobCancelledError:
        return _persist_result(_cancelled_result(job.job_id), metric=job.metric)
    except Exception as exc:
        logger.exception(
            "Generic task %s failed while executing metric %s for job %s.",
            GENERIC_TASK_NAME,
            job.metric,
            job.job_id,
        )
        return _persist_result(
            _failed_result(job.job_id, "worker", str(exc)),
            metric=job.metric,
        )

    result = _normalise_plugin_result(raw_result, job)
    return _persist_result(result, metric=job.metric)


@celery_app.task(name=GENERIC_TASK_NAME, bind=True)
def run_metric_task(self: Task, envelope_payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(getattr(self.request, "id", "") or "unknown-job")
    return execute_job(envelope_payload, task_id=task_id)


refresh_runner_registry()


__all__ = [
    "GENERIC_TASK_NAME",
    "RUNNER_REGISTRY",
    "RunnerMetricEntryV2",
    "WorkerRunContext",
    "build_run_context",
    "execute_job",
    "load_runner_metric_entries",
    "refresh_runner_registry",
    "run_metric_task",
]
