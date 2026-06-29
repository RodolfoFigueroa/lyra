import importlib
import logging
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from celery import Task
from lyra.sdk.models import (
    CancelledJobResult,
    FailedJobResult,
    FileJobResult,
    JobEnvelope,
    TableJobResult,
    TerminalJobResult,
    parse_job_result,
)
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.plugin_v2 import (
    FileMetricOutputV2,
    MetricManifestV2,
    MetricOutputV2,
    OutputColumnType,
    TableMetricOutputV2,
)
from pydantic import ValidationError as PydanticValidationError

from lyra_app import job_store
from lyra_app.celery_app import celery_app
from lyra_app.plugins import install_runner_plugins, sync_runner_repos
from lyra_app.registry import load_plugin_manifest

logger = logging.getLogger(__name__)

GENERIC_TASK_NAME = "lyra.run_metric"

MetricRunCallable = Callable[
    [JobEnvelope, "WorkerRunContext"], TerminalJobResult | dict[str, Any]
]


@dataclass(frozen=True)
class RunnerMetricEntryV2:
    metric_name: str
    queue: str
    entrypoint: str
    output: MetricOutputV2
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
        output=metric.output,
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


def _failed_result(job_id: str, error_type: str, message: str) -> FailedJobResult:
    return FailedJobResult(
        job_id=job_id,
        error={"type": error_type, "message": message},
    )


def _cancelled_result(job_id: str) -> CancelledJobResult:
    return CancelledJobResult(job_id=job_id)


def _persist_result(
    result: TerminalJobResult,
    *,
    metric: str | None = None,
) -> dict[str, Any]:
    return job_store.save_job_result(result, metric=metric)


def _cell_error(
    value: Any,
    column_type: OutputColumnType,
    *,
    nullable: bool,
) -> str | None:
    if value is None:
        return None if nullable else "null is not allowed"

    error: str | None
    if column_type == "boolean":
        error = None if type(value) is bool else "expected boolean"
    elif column_type == "integer":
        error = None if type(value) is int else "expected integer"
    elif column_type == "number":
        if not isinstance(value, int | float) or isinstance(value, bool):
            error = "expected number"
        else:
            error = None if math.isfinite(float(value)) else "number must be finite"
    elif column_type == "string":
        error = None if type(value) is str else "expected string"
    else:
        error = f"unsupported column type: {column_type}"
    return error


def _validate_table_result(
    result: TableJobResult,
    job: JobEnvelope,
    output: TableMetricOutputV2,
) -> TableJobResult | FailedJobResult:
    try:
        location = GeoJSON.model_validate(job.input["location"])
    except PydanticValidationError as exc:
        return _failed_result(job.job_id, "invalid_result", str(exc))

    expected_index = [feature.id for feature in location.features]
    if result.index != expected_index:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Table result index must match the resolved location feature IDs.",
        )

    expected_columns = [column.name for column in output.columns]
    if result.columns != expected_columns:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Table result columns must match the metric output declaration.",
        )

    for row_position, row in enumerate(result.data):
        for column_position, column in enumerate(output.columns):
            error = _cell_error(
                row[column_position],
                column.type,
                nullable=column.nullable,
            )
            if error is not None:
                return _failed_result(
                    job.job_id,
                    "invalid_result",
                    (
                        "Invalid table value at row "
                        f"{row_position}, column {column.name!r}: {error}."
                    ),
                )

    return result


def _validate_file_result(
    result: FileJobResult,
    job: JobEnvelope,
    output: FileMetricOutputV2,
    context: WorkerRunContext,
) -> FileJobResult | FailedJobResult:
    if result.media_type != output.media_type:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "File result media_type must match the metric output declaration.",
        )

    file_path = Path(result.file_path)
    if not file_path.is_absolute():
        file_path = context.temp_dir / file_path

    resolved_path = file_path.resolve()
    temp_dir = context.temp_dir.resolve()
    if not resolved_path.is_relative_to(temp_dir):
        return _failed_result(
            job.job_id,
            "invalid_result",
            "File result path must be inside the job temp directory.",
        )

    if not resolved_path.is_file():
        return _failed_result(
            job.job_id,
            "invalid_result",
            "File result path does not exist or is not a file.",
        )

    allowed_extensions = {extension.lower() for extension in output.extensions}
    if resolved_path.suffix.lower() not in allowed_extensions:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "File result extension must match the metric output declaration.",
        )

    return result.model_copy(update={"file_path": str(resolved_path)})


def _validate_success_result(
    result: TerminalJobResult,
    job: JobEnvelope,
    output: MetricOutputV2,
    context: WorkerRunContext,
) -> TerminalJobResult:
    if isinstance(result, FailedJobResult | CancelledJobResult):
        return result

    if isinstance(output, TableMetricOutputV2):
        if not isinstance(result, TableJobResult):
            return _failed_result(
                job.job_id,
                "invalid_result",
                "Metric declared table output but returned a non-table result.",
            )
        return _validate_table_result(result, job, output)

    if not isinstance(result, FileJobResult):
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Metric declared file output but returned a non-file result.",
        )
    return _validate_file_result(result, job, output, context)


def _normalise_plugin_result(
    raw_result: Any,
    job: JobEnvelope,
    entry: RunnerMetricEntryV2,
    context: WorkerRunContext,
) -> TerminalJobResult:
    try:
        result = parse_job_result(raw_result)
    except PydanticValidationError as exc:
        return _failed_result(job.job_id, "invalid_result", str(exc))

    if result.job_id != job.job_id:
        return _failed_result(
            job.job_id,
            "invalid_result",
            f"Plugin returned job_id {result.job_id!r} for job {job.job_id!r}.",
        )
    return _validate_success_result(result, job, entry.output, context)


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

    result = _normalise_plugin_result(raw_result, job, entry, context)
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
