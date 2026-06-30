import importlib
import logging
import math
import os
import re
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
from lyra.sdk.models.plugin_v3 import (
    CompiledMetricManifestV3,
    FileOutputV3,
    OutputColumnTypeV3,
    OutputSpecV3,
    TableOutputColumnV3,
    TableOutputV3,
)
from pydantic import ValidationError as PydanticValidationError

from lyra_app import job_store
from lyra_app.celery_app import celery_app
from lyra_app.config import ConfigLoadError, LyraConfig, get_config
from lyra_app.plugins import (
    install_runner_plugins,
    sync_plugin_repos,
    sync_runner_repos,
)
from lyra_app.registry import load_plugin_manifest

logger = logging.getLogger(__name__)

GENERIC_TASK_NAME = "lyra.run_metric"
_BATCHED_ITEM_FIELDS = {"key", "value", "label"}
_BATCHED_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MetricRunCallable = Callable[
    [JobEnvelope, "WorkerRunContext"], TerminalJobResult | dict[str, Any]
]


@dataclass(frozen=True)
class RunnerMetricEntry:
    metric_name: str
    queue: str
    entrypoint: str
    output: OutputSpecV3
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


RUNNER_REGISTRY: dict[str, RunnerMetricEntry] = {}


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


def _entry_from_metric(
    metric: CompiledMetricManifestV3,
    *,
    queue: str,
) -> RunnerMetricEntry:
    return RunnerMetricEntry(
        metric_name=metric.name,
        queue=queue,
        entrypoint=metric.entrypoint,
        output=metric.output,
        run=_load_entrypoint(metric.entrypoint),
    )


def _runner_sync_repos(worker_name: str | None, config: LyraConfig | None) -> list[Any]:
    if worker_name is None or config is None:
        return sync_runner_repos()
    return sync_plugin_repos(
        config.worker_install_dir(worker_name),
        config.plugins.repos,
    )


def _runner_queue_assignments(config: LyraConfig | None) -> dict[str, str]:
    return {} if config is None else config.plugins.metric_queues


def _runner_queues(worker_name: str | None, config: LyraConfig | None) -> set[str]:
    if worker_name is None or config is None:
        return _configured_runner_queues()
    return set(config.get_worker(worker_name).queues)


def _resolve_metric_queue(
    metric: CompiledMetricManifestV3,
    metric_queues: dict[str, str],
) -> str:
    try:
        return metric_queues[metric.name]
    except KeyError as exc:
        msg = (
            f"Metric {metric.name!r} has no queue assignment. Run API catalog "
            "refresh before starting workers."
        )
        raise RuntimeError(msg) from exc


def load_runner_metric_entries(
    worker_name: str | None = None,
    *,
    config: LyraConfig | None = None,
) -> dict[str, RunnerMetricEntry]:
    if worker_name is not None and config is None:
        config = get_config()

    queues = _runner_queues(worker_name, config)
    metric_queues = _runner_queue_assignments(config)
    repos = install_runner_plugins(_runner_sync_repos(worker_name, config))
    entries: dict[str, RunnerMetricEntry] = {}

    for repo in repos:
        manifest = load_plugin_manifest(repo.path)
        for metric in manifest.metrics:
            queue = _resolve_metric_queue(metric, metric_queues)
            if queues and queue not in queues:
                continue
            if metric.name in entries:
                msg = f"Duplicate metric name in runner manifests: {metric.name!r}"
                raise RuntimeError(msg)
            entries[metric.name] = _entry_from_metric(metric, queue=queue)

    return entries


def refresh_runner_registry(
    worker_name: str | None = None,
    *,
    config: LyraConfig | None = None,
) -> dict[str, RunnerMetricEntry]:
    registry = load_runner_metric_entries(worker_name, config=config)
    RUNNER_REGISTRY.clear()
    RUNNER_REGISTRY.update(registry)
    logger.info(
        "Loaded %d v3 runner metric(s) for generic task %s.",
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
    column_type: OutputColumnTypeV3,
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


def _batched_template_context(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        msg = "Batched column source values must be objects."
        raise TypeError(msg)

    invalid_fields = sorted(set(value) - _BATCHED_ITEM_FIELDS)
    if invalid_fields:
        names = ", ".join(invalid_fields)
        msg = f"Batched column source values contain unsupported fields: {names}."
        raise ValueError(msg)

    key = value.get("key")
    if not isinstance(key, str) or not _BATCHED_KEY_PATTERN.fullmatch(key):
        msg = (
            "Batched column source value 'key' must be a non-empty string matching "
            "Lyra's batched key pattern."
        )
        raise ValueError(msg)

    if "value" not in value:
        msg = "Batched column source values must contain 'value'."
        raise ValueError(msg)

    label = value.get("label", key)
    if not isinstance(label, str):
        msg = "Batched column source value 'label' must be a string when provided."
        raise TypeError(msg)

    return {"key": key, "label": label}


def _expand_batched_template(template: str, context: dict[str, str]) -> str:
    value = template
    for name, replacement in context.items():
        value = value.replace(f"{{{name}}}", replacement)
    return value


def _expand_table_output_columns(
    output: TableOutputV3,
    job_input: dict[str, Any],
) -> list[TableOutputColumnV3]:
    columns = list(output.columns)

    for column_group in output.batched_columns:
        source_values = job_input.get(column_group.source)
        if not isinstance(source_values, list):
            msg = (
                f"Batched column source {column_group.source!r} must be present "
                "as an array."
            )
            raise TypeError(msg)

        for source_value in source_values:
            template_context = _batched_template_context(source_value)
            name = _expand_batched_template(
                column_group.name,
                template_context,
            )
            if not name:
                msg = "Batched column templates must produce non-empty names."
                raise ValueError(msg)
            description = _expand_batched_template(
                column_group.description,
                template_context,
            )
            columns.append(
                TableOutputColumnV3(
                    name=name,
                    type=column_group.type,
                    unit=column_group.unit,
                    description=description,
                    nullable=column_group.nullable,
                )
            )

    names = [column.name for column in columns]
    if len(names) != len(set(names)):
        msg = "Expanded table output columns must be unique."
        raise ValueError(msg)

    return columns


def _expected_table_index(
    job: JobEnvelope,
) -> list[str] | FailedJobResult:
    try:
        location = GeoJSON.model_validate(job.input["location"])
    except PydanticValidationError as exc:
        return _failed_result(job.job_id, "invalid_result", str(exc))

    expected_index = [str(feature.id) for feature in location.features]
    if len(expected_index) != len(set(expected_index)):
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Resolved location feature IDs must be unique after string conversion.",
        )

    return expected_index


def _validate_table_result(
    result: TableJobResult,
    job: JobEnvelope,
    output: TableOutputV3,
) -> TableJobResult | FailedJobResult:
    expected_index = _expected_table_index(job)
    if isinstance(expected_index, FailedJobResult):
        return expected_index

    if result.index != expected_index:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Table result index must match the resolved location feature IDs.",
        )

    try:
        expanded_columns = _expand_table_output_columns(output, job.input)
    except (TypeError, ValueError) as exc:
        return _failed_result(job.job_id, "invalid_result", str(exc))

    expected_columns = [column.name for column in expanded_columns]
    if result.columns != expected_columns:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Table result columns must match the metric output declaration.",
        )

    for row_position, row in enumerate(result.data):
        for column_position, column in enumerate(expanded_columns):
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
    output: FileOutputV3,
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
    output: OutputSpecV3,
    context: WorkerRunContext,
) -> TerminalJobResult:
    if isinstance(result, FailedJobResult | CancelledJobResult):
        return result

    if isinstance(output, TableOutputV3):
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
    entry: RunnerMetricEntry,
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


try:
    refresh_runner_registry()
except ConfigLoadError:
    logger.info("Skipping runner registry preload because Lyra config is unavailable.")


__all__ = [
    "GENERIC_TASK_NAME",
    "RUNNER_REGISTRY",
    "RunnerMetricEntry",
    "WorkerRunContext",
    "build_run_context",
    "execute_job",
    "load_runner_metric_entries",
    "refresh_runner_registry",
    "run_metric_task",
]
