"""Celery task implementations for executing metric jobs."""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from celery import Task
from celery.signals import task_failure
from lyra.sdk.db import LyraDB
from lyra.sdk.errors import MetricInputError, MetricResultError
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.job import (
    CancelledJobResult,
    FailedJobResult,
    JobEnvelope,
    JobProgress,
    TerminalJobResult,
)
from lyra.sdk.models.plugin import MetricManifest, PluginManifest
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.plugin_loader import load_plugin_definition
from lyra.sdk.types import JsonObject, JsonValue
from pydantic import ValidationError as PydanticValidationError

from lyra_app import job_store
from lyra_app.celery_app import celery_app
from lyra_app.config import LyraConfig, get_config
from lyra_app.db import connection as database_connection
from lyra_app.db.client import LyraDBImplicit
from lyra_app.db.connection import is_database_unavailable_error
from lyra_app.plugins import MANIFEST_FILENAME, load_installed_plugins

logger = logging.getLogger(__name__)

GENERIC_TASK_NAME = "lyra.run_metric"


@dataclass(frozen=True)
class RunnerMetricEntry:
    """Bundle a runner metric's queue, output contract, and callable."""

    metric_name: str
    queue: str
    definition: PluginDefinition


@dataclass
class WorkerRunContext:
    """Provide plugin execution resources and coalesced progress reporting."""

    job_id: str
    metric: str
    logger: logging.Logger | logging.LoggerAdapter
    temp_dir: Path
    db: LyraDB
    _pending_progress: JobProgress | None = field(default=None, init=False, repr=False)
    _last_progress_emit: float | None = field(default=None, init=False, repr=False)

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        """Validate and coalesce optional progress snapshots."""
        self._pending_progress = JobProgress(
            timestamp=datetime.now(UTC),
            stage=stage,
            current=current,
            total=total,
            unit=unit,
            message=message,
        )
        if self._last_progress_emit is None or (
            (time.monotonic() - self._last_progress_emit) * 1000
            >= get_config().job_progress.min_interval_ms
        ):
            self.flush_progress()

    def flush_progress(self) -> None:
        """Persist the latest pending snapshot before leaving execution."""
        if self._pending_progress is not None:
            job_store.update_job_progress(self.job_id, self._pending_progress)
            self._last_progress_emit = time.monotonic()
            self._pending_progress = None

    def check_cancelled(self) -> None:
        """Raise when the job has been marked cancelled in durable state."""
        job_store.raise_if_cancelled(self.job_id)


RUNNER_REGISTRY: dict[str, RunnerMetricEntry] = {}
_RUNNER_TEMP_BASE: Path | None = None


def set_runner_temp_base(path: Path | None) -> None:
    """Set or clear the process-wide parent directory for per-job scratch data."""
    global _RUNNER_TEMP_BASE  # ruff:ignore[global-statement]

    _RUNNER_TEMP_BASE = path


def _entry_from_metric(
    metric: MetricManifest,
    *,
    queue: str,
    definition: PluginDefinition,
) -> RunnerMetricEntry:
    return RunnerMetricEntry(
        metric_name=metric.name,
        queue=queue,
        definition=definition,
    )


def _validated_plugin_definition(
    manifest: PluginManifest,
) -> PluginDefinition:
    definition = load_plugin_definition(manifest.factory)
    live_manifest = definition.manifest(
        plugin=manifest.plugin,
        factory=manifest.factory,
    )
    if live_manifest.model_dump(mode="json") != manifest.model_dump(mode="json"):
        msg = (
            f"Plugin factory {manifest.factory!r} does not match {MANIFEST_FILENAME}. "
            "Run 'lyra-plugin build-manifest' in the plugin project."
        )
        raise RuntimeError(msg)
    return definition


def load_runner_metric_entries(
    worker_name: str,
    *,
    config: LyraConfig | None = None,
) -> dict[str, RunnerMetricEntry]:
    """Load executable metric entries assigned to one worker pool.

    Returns:
        A mapping from metric name to its validated runner contract.

    Raises:
        RuntimeError: If runner manifests contain duplicate selected metrics.
    """
    if config is None:
        config = get_config()

    queues = set(config.get_worker(worker_name).queues)
    plugins = load_installed_plugins(config.plugins)
    entries: dict[str, RunnerMetricEntry] = {}

    for plugin in plugins:
        manifest = plugin.manifest
        selected_metrics = [
            (metric, plugin.metric_queues[metric.name])
            for metric in manifest.metrics
            if plugin.metric_queues[metric.name] in queues
        ]
        if not selected_metrics:
            continue
        definition = _validated_plugin_definition(manifest)
        for metric, queue in selected_metrics:
            if metric.name in entries:
                msg = f"Duplicate metric name in runner manifests: {metric.name!r}"
                raise RuntimeError(msg)
            entries[metric.name] = _entry_from_metric(
                metric,
                queue=queue,
                definition=definition,
            )

    return entries


def refresh_runner_registry(
    worker_name: str,
    *,
    config: LyraConfig | None = None,
) -> dict[str, RunnerMetricEntry]:
    """Replace the process runner registry for one configured worker pool.

    Returns:
        A copy of the newly loaded runner registry.
    """
    global _RUNNER_TEMP_BASE  # ruff:ignore[global-statement]

    if config is None:
        config = get_config()

    registry = load_runner_metric_entries(worker_name, config=config)
    RUNNER_REGISTRY.clear()
    RUNNER_REGISTRY.update(registry)
    _RUNNER_TEMP_BASE = config.worker_temp_dir(worker_name)
    logger.info(
        "Loaded %d runner metric(s) for generic task %s.",
        len(RUNNER_REGISTRY),
        GENERIC_TASK_NAME,
    )
    return dict(RUNNER_REGISTRY)


def _runner_temp_base() -> Path:
    if _RUNNER_TEMP_BASE is not None:
        return _RUNNER_TEMP_BASE

    msg = "Runner temp base is not configured. Start workers with worker_launcher."
    raise RuntimeError(msg)


def _safe_path_segment(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "_"
        for character in value
    )


def build_run_context(job: JobEnvelope) -> WorkerRunContext:
    """Build the isolated filesystem, database, logging, and progress context for a job.

    Returns:
        A worker context bound to the job and its per-job scratch directory.
    """
    temp_dir = _runner_temp_base() / _safe_path_segment(job.job_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return WorkerRunContext(
        job_id=job.job_id,
        metric=job.metric,
        logger=logging.LoggerAdapter(
            logging.getLogger(f"{__name__}.{job.metric}"),
            {"structured_fields": {"job_id": job.job_id, "metric": job.metric}},
        ),
        temp_dir=temp_dir,
        db=_build_db_context(),
    )


def _build_db_context() -> LyraDB:
    return LyraDBImplicit(database_connection.get_worker_engine())


def _job_id_from_payload(payload: JsonValue, fallback: str) -> str:
    if isinstance(payload, dict):
        job_id = payload.get("job_id")
        if isinstance(job_id, str) and job_id:
            return job_id
    return fallback


def _failed_result(job_id: str, error_type: str, message: str) -> FailedJobResult:
    return FailedJobResult(
        job_id=job_id,
        error={"type": error_type, "message": message},
    )


def _database_unavailable_result(job_id: str) -> FailedJobResult:
    return FailedJobResult(
        job_id=job_id,
        error={
            "type": "database_unavailable",
            "message": "The database is temporarily unavailable.",
            "retryable": True,
        },
    )


def _cancelled_result(job_id: str) -> CancelledJobResult:
    return CancelledJobResult(job_id=job_id)


def _persist_result(
    result: TerminalJobResult,
    *,
    metric: str | None = None,
) -> JsonObject:
    del metric
    return job_store.save_job_result(result)


def _flush_failed_job_progress(
    context: WorkerRunContext | None,
    job: JobEnvelope,
) -> bool:
    if context is None:
        return False
    try:
        context.flush_progress()
    except job_store.JobCancelledError:
        return True
    except Exception:
        logger.exception(
            "Could not flush progress for failed job %s.",
            job.job_id,
        )
    return False


def _execute_known_job(job: JobEnvelope, entry: RunnerMetricEntry) -> JsonObject:
    if job_store.is_job_cancelled(job.job_id):
        return _persist_result(_cancelled_result(job.job_id), metric=job.metric)

    context: WorkerRunContext | None = None
    try:
        context = build_run_context(job)
        raw_result = entry.definition(job, context)
        context.flush_progress()
        result = entry.definition.normalize_result(
            job.metric,
            raw_result,
            job_id=job.job_id,
            location=(
                GeoJSON.model_validate(job.input["location"])
                if "location" in job.input
                else None
            ),
            temp_dir=context.temp_dir,
            location_areas_m2=job.location_areas_m2,
        )
    except job_store.JobCancelledError:
        return _persist_result(_cancelled_result(job.job_id), metric=job.metric)
    except Exception as exc:
        if _flush_failed_job_progress(context, job):
            return _persist_result(_cancelled_result(job.job_id), metric=job.metric)
        if isinstance(exc, MetricInputError | MetricResultError):
            failure = FailedJobResult(
                job_id=job.job_id,
                error={
                    "type": "invalid_input"
                    if isinstance(exc, MetricInputError)
                    else "invalid_result",
                    "message": exc.message,
                    "path": exc.path,
                },
            )
        elif is_database_unavailable_error(exc):
            logger.warning(
                "Database unavailable while executing metric %s for job %s.",
                job.metric,
                job.job_id,
                exc_info=True,
            )
            failure = _database_unavailable_result(job.job_id)
        else:
            logger.exception(
                "Generic task %s failed while executing metric %s for job %s.",
                GENERIC_TASK_NAME,
                job.metric,
                job.job_id,
            )
            failure = _failed_result(
                job.job_id, "worker", "Metric execution failed unexpectedly."
            )
        return _persist_result(failure, metric=job.metric)

    return _persist_result(result, metric=job.metric)


def execute_job(envelope_payload: JsonValue, *, task_id: str) -> JsonObject:
    """Validate and execute a serialized job through the captured runner registry.

    Returns:
        The persisted terminal result payload, including validation failures.
    """
    fallback_job_id = _job_id_from_payload(envelope_payload, task_id)
    try:
        job = JobEnvelope.model_validate(envelope_payload)
    except PydanticValidationError as exc:
        return _persist_result(
            _failed_result(fallback_job_id, "invalid_envelope", str(exc))
        )

    if not job_store.claim_job(job.job_id):
        return job_store.get_job_result(job.job_id) or {
            "job_id": job.job_id,
            "detail": "Execution was not claimed.",
        }

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
    return _execute_known_job(job, entry)


@celery_app.task(name=GENERIC_TASK_NAME, bind=True)
def run_metric_task(self: Task, envelope_payload: JsonObject) -> JsonObject:
    """Execute a generic Celery metric task using its request identifier.

    Returns:
        The persisted terminal result payload.
    """
    task_id = str(getattr(self.request, "id", "") or "unknown-job")
    return execute_job(envelope_payload, task_id=task_id)


@task_failure.connect(sender=run_metric_task)
def _notify_unexpected_task_failure(
    task_id: str | None = None,
    **_: object,
) -> None:
    if not task_id:
        logger.error("Celery reported a task failure without a task ID.")
        return
    from lyra_app.worker_control import (  # ruff:ignore[import-outside-top-level]
        notify_unexpected_task_failure,
    )

    notify_unexpected_task_failure(task_id)
