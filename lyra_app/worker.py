import importlib
import logging
import math
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
    CompiledPluginManifestV3,
    FileOutputV3,
    OutputColumnTypeV3,
    OutputSpecV3,
    TableOutputColumnV3,
    TableOutputV3,
    expand_runner_table_output_columns,
    expand_table_output_columns,
)
from lyra.sdk.plugin import PluginDefinition
from pydantic import ValidationError as PydanticValidationError

from lyra_app import job_store
from lyra_app.celery_app import celery_app
from lyra_app.config import ConfigLoadError, LyraConfig, get_config
from lyra_app.plugin_state import (
    PluginState,
    PluginStateStore,
    metric_queue_mapping,
    repo_record_to_source,
)
from lyra_app.plugins import (
    MANIFEST_FILENAME,
    install_runner_plugins,
    sync_plugin_repos,
)
from lyra_app.registry import load_plugin_manifest

logger = logging.getLogger(__name__)

GENERIC_TASK_NAME = "lyra.run_metric"
FRACTION_RANGE_TOLERANCE = 1e-9

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
_RUNNER_TEMP_BASE: Path | None = None


def set_runner_temp_base(path: Path | None) -> None:
    global _RUNNER_TEMP_BASE  # noqa: PLW0603

    _RUNNER_TEMP_BASE = path


def _load_entrypoint(spec: str) -> PluginDefinition:
    module_name, sep, function_name = spec.partition(":")
    if not sep or not module_name or not function_name:
        msg = f"Entrypoint must use 'module:function' format: {spec!r}"
        raise ValueError(msg)

    value = getattr(importlib.import_module(module_name), function_name)
    if not isinstance(value, PluginDefinition):
        msg = f"Entrypoint {spec!r} did not resolve to a PluginDefinition."
        raise TypeError(msg)
    return value


def _entry_from_metric(
    metric: CompiledMetricManifestV3,
    *,
    queue: str,
    definition: PluginDefinition,
) -> RunnerMetricEntry:
    return RunnerMetricEntry(
        metric_name=metric.name,
        queue=queue,
        entrypoint=metric.entrypoint,
        output=metric.output,
        run=definition,
    )


def _validated_plugin_definition(
    manifest: CompiledPluginManifestV3,
) -> PluginDefinition:
    entrypoints = {metric.entrypoint for metric in manifest.metrics}
    if len(entrypoints) != 1:
        msg = "Generated plugin manifests must use one PluginDefinition entrypoint."
        raise RuntimeError(msg)
    entrypoint = next(iter(entrypoints))
    definition = _load_entrypoint(entrypoint)
    live_manifest = definition.compiled_manifest(
        plugin=manifest.plugin,
        entrypoint=entrypoint,
    )
    if live_manifest.model_dump(mode="json") != manifest.model_dump(mode="json"):
        msg = (
            f"Plugin definition {entrypoint!r} does not match {MANIFEST_FILENAME}. "
            "Run 'lyra-plugin build-manifest' in the plugin project."
        )
        raise RuntimeError(msg)
    return definition


def _runner_sync_repos(
    worker_name: str,
    config: LyraConfig,
    state: PluginState,
) -> list[Any]:
    raw_entries = [repo_record_to_source(repo) for repo in state.repos if repo.enabled]
    return sync_plugin_repos(
        config.worker_install_dir(worker_name),
        raw_entries,
        raise_on_error=True,
    )


def _runner_queue_assignments(state: PluginState) -> dict[str, str]:
    return metric_queue_mapping(state)


def _runner_queues(worker_name: str, config: LyraConfig) -> set[str]:
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
    worker_name: str,
    *,
    config: LyraConfig | None = None,
    store: PluginStateStore | None = None,
) -> dict[str, RunnerMetricEntry]:
    if config is None:
        config = get_config()

    state_store = store or PluginStateStore(
        allowed_queues=config.plugins.allowed_queues,
    )
    state = state_store.load()
    queues = _runner_queues(worker_name, config)
    metric_queues = _runner_queue_assignments(state)
    repos = install_runner_plugins(_runner_sync_repos(worker_name, config, state))
    entries: dict[str, RunnerMetricEntry] = {}

    for repo in repos:
        manifest = load_plugin_manifest(repo.path)
        selected_metrics: list[tuple[CompiledMetricManifestV3, str]] = []
        for metric in manifest.metrics:
            queue = _resolve_metric_queue(metric, metric_queues)
            if queues and queue not in queues:
                continue
            selected_metrics.append((metric, queue))
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
    store: PluginStateStore | None = None,
) -> dict[str, RunnerMetricEntry]:
    global _RUNNER_TEMP_BASE  # noqa: PLW0603

    if config is None:
        config = get_config()

    registry = load_runner_metric_entries(worker_name, config=config, store=store)
    RUNNER_REGISTRY.clear()
    RUNNER_REGISTRY.update(registry)
    _RUNNER_TEMP_BASE = config.worker_temp_dir(worker_name)
    logger.info(
        "Loaded %d v3 runner metric(s) for generic task %s.",
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
    temp_dir = _runner_temp_base() / _safe_path_segment(job.job_id)
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
        from lyra_app.db.connection import get_worker_engine  # noqa: PLC0415
    except (ConfigLoadError, KeyError) as exc:
        logger.info("DB context unavailable: %s.", exc)
        return None
    return LyraDBImplicit(get_worker_engine())


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


def _validate_table_values(
    result: TableJobResult,
    columns: list[TableOutputColumnV3],
) -> str | None:
    for row_position, row in enumerate(result.data):
        for column_position, column in enumerate(columns):
            error = _cell_error(
                row[column_position],
                column.type,
                nullable=column.nullable,
            )
            if error is not None:
                return (
                    "Invalid table value at row "
                    f"{row_position}, column {column.name!r}: {error}."
                )
    return None


def _derive_fractional_area_columns(
    result: TableJobResult,
    job: JobEnvelope,
    runner_columns: list[TableOutputColumnV3],
) -> TableJobResult | FailedJobResult:
    if not any(column.derivations for column in runner_columns):
        return result

    areas = job.location_areas_m2
    if areas is None:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Job envelope is missing server-calculated location areas.",
        )
    if list(areas) != result.index:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Location area feature IDs must match the table result index.",
        )

    derived_columns: list[str] = []
    derived_data: list[list[Any]] = [[] for _ in result.data]
    for column_position, column in enumerate(runner_columns):
        derived_columns.append(column.name)
        for row_position, row in enumerate(result.data):
            derived_data[row_position].append(row[column_position])

        for derivation in column.derivations:
            derived_columns.append(derivation.name)
            for row_position, (feature_id, row) in enumerate(
                zip(result.index, result.data, strict=True)
            ):
                source_value = row[column_position]
                if source_value is None:
                    derived_data[row_position].append(None)
                    continue

                area = areas[feature_id]
                if not math.isfinite(area) or area <= 0:
                    return _failed_result(
                        job.job_id,
                        "invalid_result",
                        f"Location area for feature {feature_id!r} must be positive.",
                    )
                fraction = float(source_value) / area
                if (
                    fraction < -FRACTION_RANGE_TOLERANCE
                    or fraction > 1 + FRACTION_RANGE_TOLERANCE
                ):
                    return _failed_result(
                        job.job_id,
                        "invalid_result",
                        (
                            f"Derived fraction for feature {feature_id!r}, source "
                            f"column {column.name!r} is outside [0, 1]: {fraction}."
                        ),
                    )
                derived_data[row_position].append(min(1.0, max(0.0, fraction)))

    return TableJobResult(
        job_id=result.job_id,
        index=result.index,
        columns=derived_columns,
        data=derived_data,
    )


def _validate_result_against_columns(
    result: TableJobResult,
    job: JobEnvelope,
    columns: list[TableOutputColumnV3],
    *,
    mismatch_message: str,
) -> FailedJobResult | None:
    expected_columns = [column.name for column in columns]
    if result.columns != expected_columns:
        return _failed_result(
            job.job_id,
            "invalid_result",
            mismatch_message,
        )
    error = _validate_table_values(result, columns)
    if error is not None:
        return _failed_result(job.job_id, "invalid_result", error)
    return None


def _validate_result_index(
    result: TableJobResult,
    job: JobEnvelope,
) -> FailedJobResult | None:
    expected_index = _expected_table_index(job)
    if isinstance(expected_index, FailedJobResult):
        return expected_index
    if result.index != expected_index:
        return _failed_result(
            job.job_id,
            "invalid_result",
            "Table result index must match the resolved location feature IDs.",
        )
    return None


def _validate_table_result(
    result: TableJobResult,
    job: JobEnvelope,
    output: TableOutputV3,
) -> TableJobResult | FailedJobResult:
    index_error = _validate_result_index(result, job)
    if index_error is not None:
        return index_error

    try:
        runner_columns = expand_runner_table_output_columns(output, job.input)
        expanded_columns = expand_table_output_columns(output, job.input)
    except (TypeError, ValueError) as exc:
        return _failed_result(job.job_id, "invalid_result", str(exc))

    validation_error = _validate_result_against_columns(
        result,
        job,
        runner_columns,
        mismatch_message=(
            "Table result columns must match the runner output declaration."
        ),
    )
    if validation_error is not None:
        return validation_error

    derived_result = _derive_fractional_area_columns(result, job, runner_columns)
    if isinstance(derived_result, FailedJobResult):
        return derived_result

    validation_error = _validate_result_against_columns(
        derived_result,
        job,
        expanded_columns,
        mismatch_message=(
            "Derived table columns must match the effective output declaration."
        ),
    )
    if validation_error is not None:
        return validation_error

    return derived_result


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
    "set_runner_temp_base",
]
