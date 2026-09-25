"""RQ entry points for executing metric jobs."""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lyra.sdk.db import LyraDB
from lyra.sdk.errors import MetricInputError, MetricResultError
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.job import (
    FailedJobResult,
    JobEnvelope,
    JobProgress,
)
from lyra.sdk.models.plugin import MetricManifest, PluginManifest
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.plugin_loader import load_plugin_definition
from lyra.sdk.types import JsonObject
from redis.exceptions import RedisError
from rq import get_current_job
from rq.job import Job

from lyra_app.config import LyraConfig, get_config
from lyra_app.db import connection as database_connection
from lyra_app.db.client import LyraDBImplicit
from lyra_app.db.connection import is_database_unavailable_error
from lyra_app.plugins import MANIFEST_FILENAME, load_installed_plugins

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerMetricEntry:
    """Bundle a runner metric's queue, output contract, and callable."""

    metric_name: str
    queue: str
    contract: MetricManifest
    plugin: PluginManifest
    distribution: str
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
            >= get_config().jobs.progress_min_interval_ms
        ):
            self.flush_progress()

    def flush_progress(self) -> None:
        """Best-effort publish at most once per configured interval."""
        now = time.monotonic()
        if self._last_progress_emit is not None and (
            (now - self._last_progress_emit) * 1000
            < get_config().jobs.progress_min_interval_ms
        ):
            return
        if self._pending_progress is None:
            return
        self._last_progress_emit = now
        native = get_current_job()
        if native is not None:
            native.meta["progress"] = self._pending_progress.model_dump(mode="json")
            try:
                native.save_meta()
            except RedisError:
                logger.warning(
                    "Progress persistence failed for %s", self.job_id, exc_info=True
                )
        self._pending_progress = None


RUNNER_REGISTRY: dict[str, RunnerMetricEntry] = {}
_RUNNER_TEMP_BASE: Path | None = None


def set_runner_temp_base(path: Path | None) -> None:
    """Set or clear the process-wide parent directory for per-job scratch data."""
    global _RUNNER_TEMP_BASE  # ruff:ignore[global-statement]

    _RUNNER_TEMP_BASE = path


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
            entries[metric.name] = RunnerMetricEntry(
                metric_name=metric.name,
                contract=metric,
                plugin=manifest,
                distribution=plugin.distribution,
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
        "Loaded %d runner metric(s).",
        len(RUNNER_REGISTRY),
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


def _save_failure(job_id: str, exc: Exception) -> None:
    if isinstance(exc, MetricInputError | MetricResultError):
        error = {
            "type": "invalid_input"
            if isinstance(exc, MetricInputError)
            else "invalid_result",
            "message": exc.message,
            "path": exc.path,
        }
    elif is_database_unavailable_error(exc):
        error = {
            "type": "database_unavailable",
            "message": "The database is temporarily unavailable.",
            "retryable": True,
        }
    else:
        error = {"type": "worker", "message": "Job execution failed."}
    native = get_current_job()
    if native is not None:
        native.meta["failure"] = FailedJobResult(job_id=job_id, error=error).model_dump(
            mode="json"
        )
        try:
            native.save_meta()
        except RedisError:
            logger.warning(
                "Failure metadata persistence failed for %s", job_id, exc_info=True
            )


def run_metric_task(envelope_payload: JsonObject) -> JsonObject:
    """Execute the installed plugin and return an SDK-validated result to RQ.

    Returns:
        A JSON payload for RQ's native successful result storage.

    Raises:
        RuntimeError: Outside RQ or when the submitted plugin contract differs.
    """
    native = get_current_job()
    if native is None:
        msg = "Metric execution requires an RQ worker."
        raise RuntimeError(msg)
    native.meta["worker_id"] = native.worker_name
    try:
        native.save_meta()
    except RedisError:
        logger.warning("Could not save worker identity for %s", native.id)
    try:
        return _execute_metric(envelope_payload, native)
    except Exception as exc:
        _save_failure(native.id, exc)
        raise
    finally:
        database_connection.dispose_worker_engine()


def _execute_metric(envelope_payload: JsonObject, native: Job) -> JsonObject:
    job = JobEnvelope.model_validate(envelope_payload)
    entry = RUNNER_REGISTRY.get(job.metric)
    provenance = native.meta.get("provenance", {})
    if entry is None:
        msg = "Metric is unavailable in this worker installation."
        raise RuntimeError(msg)
    if (
        job.job_id != native.id
        or entry.queue != native.origin
        or entry.contract.model_dump(mode="json") != native.meta.get("contract")
        or entry.distribution != native.meta.get("distribution")
        or entry.plugin.plugin.model_dump(mode="json") != provenance.get("plugin")
    ):
        msg = "Submitted metric identity or contract differs from the worker."
        raise RuntimeError(msg)
    context = build_run_context(job)
    raw_result = entry.definition(job, context)
    result = entry.definition.normalize_result(
        job.metric,
        raw_result,
        job_id=job.job_id,
        location=GeoJSON.model_validate(job.input["location"])
        if "location" in job.input
        else None,
        temp_dir=context.temp_dir,
        location_areas_m2=job.location_areas_m2,
    )
    return result.model_dump(mode="json")
