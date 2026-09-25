"""Job validation, preparation, and submission workflows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NotRequired, TypedDict, Unpack
from uuid import uuid4

from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.job import (
    JobCreateRequest,
    JobCreateResponse,
    JobEnvelope,
    JobLinks,
    JobRunProvenance,
)
from lyra.sdk.models.plugin import OutputSpec, PluginInfo, TableOutput
from lyra.utils.geometry import calculate_feature_areas_m2
from redis.exceptions import RedisError

from lyra_app import job_store
from lyra_app.converters import build_converter_map
from lyra_app.registry import get_metric_entry, validate_metric_entry_payload
from lyra_app.spatial_inputs import (
    SpatialInputResolution,
    SpatialInputValidationError,
    resolve_spatial_inputs_with_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.sdk.models.plugin import SpatialInputKind
    from lyra.sdk.types import JsonObject

    from lyra_app.db.connection import ApplicationDatabaseRuntime
    from lyra_app.registry import MetricRegistryEntry


class SubmissionOptions(TypedDict):
    """Optional identifier factory for submission."""

    job_id_factory: NotRequired[Callable[[], str] | None]


class BuildSubmissionOptions(TypedDict):
    """Identifiers and computed geometry metadata for persisted job records."""

    job_id: str
    location_areas_m2: dict[str, float] | None


class UnknownMetricError(Exception):
    """Indicate that a submission names no metric in the active catalog."""

    def __init__(self, metric: str) -> None:
        """Initialize the error with the unknown metric name."""
        self.metric = metric
        super().__init__(f"Unknown metric: {metric}")


class SubmissionUnavailableError(Exception):
    """Indicate that submission cannot proceed because Redis is unavailable."""

    def __init__(self) -> None:
        """Initialize the standard temporary-unavailability error."""
        super().__init__(
            "Submission could not be confirmed. "
            "The job may have been accepted; "
            "another submission may duplicate it."
        )


def job_links(job_id: str) -> JobLinks:
    """Build relative API links for a job and its event and result resources.

    Returns:
        The canonical link set rooted at the job resource.
    """
    base = f"/jobs/{job_id}"
    return JobLinks(self=base, result=f"{base}/result")


async def _resolve_spatial_input(
    validated_input: JsonObject,
    spatial_inputs: dict[str, SpatialInputKind],
    database: ApplicationDatabaseRuntime,
) -> SpatialInputResolution:
    converter_map = build_converter_map(database.require_spatial_engine())
    return await database.run_spatial(
        resolve_spatial_inputs_with_metadata,
        validated_input,
        spatial_inputs,
        converter_map=converter_map,
    )


def _requires_location_areas(output: OutputSpec) -> bool:
    return isinstance(output, TableOutput) and any(
        column.derivations for column in output.columns
    )


async def _calculate_location_areas(
    resolved_input: JsonObject,
) -> dict[str, float]:
    location = GeoJSON.model_validate(resolved_input["location"])
    try:
        return await asyncio.to_thread(calculate_feature_areas_m2, location)
    except ValueError as exc:
        raise SpatialInputValidationError(
            [{"loc": ["location"], "msg": str(exc), "type": "value_error"}]
        ) from exc


def _build_submission_records(
    request: JobCreateRequest,
    entry: MetricRegistryEntry,
    validated_input: JsonObject,
    resolution: SpatialInputResolution,
    **options: Unpack[BuildSubmissionOptions],
) -> tuple[JobEnvelope, JobRunProvenance]:
    job_id = options["job_id"]
    envelope = JobEnvelope(
        job_id=job_id,
        metric=request.metric,
        input=resolution.input,
        location_areas_m2=options["location_areas_m2"],
    )
    provenance = JobRunProvenance(
        metric=request.metric,
        catalog_fingerprint=entry.catalog_fingerprint,
        plugin=PluginInfo(name=entry.plugin_name, version=entry.plugin_version),
        input=validated_input,
        output=entry.metric.output,
        created_at=datetime.now(UTC),
        row_identity=resolution.row_identity,
    )
    return envelope, provenance


async def _prepare_spatial_submission(
    validated_input: JsonObject,
    entry: MetricRegistryEntry,
    database: ApplicationDatabaseRuntime,
) -> tuple[SpatialInputResolution, dict[str, float] | None]:
    resolution = await _resolve_spatial_input(
        validated_input,
        entry.metric.spatial_inputs,
        database,
    )
    location_areas_m2 = None
    if _requires_location_areas(entry.metric.output):
        location_areas_m2 = await _calculate_location_areas(resolution.input)
    return resolution, location_areas_m2


async def submit_job(
    request: JobCreateRequest,
    *,
    database: ApplicationDatabaseRuntime,
    **options: Unpack[SubmissionOptions],
) -> JobCreateResponse:
    """Validate and resolve inputs before independently enqueueing one job.

    Returns:
        The accepted job identifier and public links.

    Raises:
        UnknownMetricError: If the metric is absent from the installed catalog.
        SubmissionUnavailableError: If enqueueing fails or its outcome is uncertain.
    """
    entry = get_metric_entry(request.metric)
    if entry is None:
        raise UnknownMetricError(request.metric)
    validated_input = validate_metric_entry_payload(entry, request.input)
    resolution, areas = await _prepare_spatial_submission(
        validated_input, entry, database
    )
    factory = options.get("job_id_factory") or (lambda: uuid4().hex)
    job_id = factory()
    envelope, provenance = _build_submission_records(
        request,
        entry,
        validated_input,
        resolution,
        job_id=job_id,
        location_areas_m2=areas,
    )
    try:
        await asyncio.to_thread(job_store.enqueue, envelope, provenance, entry)
    except RedisError as exc:
        raise SubmissionUnavailableError from exc
    return JobCreateResponse(
        job_id=job_id, metric=request.metric, status="queued", links=job_links(job_id)
    )
