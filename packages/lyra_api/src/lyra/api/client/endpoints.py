"""Internal endpoint definitions and transport-independent response validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from lyra.api.client.base import parse_retry_after, service_unavailable_error
from lyra.api.exceptions import DownloadError, ServiceUnavailableError
from lyra.sdk.models.admin import InstalledPluginListResponse, PluginRoutingResponse
from lyra.sdk.models.data_types import DataTypesResponse
from lyra.sdk.models.job import (
    AdminJobDetail,
    JobCreateResponse,
    JobLifecycleStatus,
    JobListResponse,
    JobStatusInfo,
    ResultDescriptor,
    TerminalJobResult,
)
from lyra.sdk.models.lookups import MetZoneCodeResponse
from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfo
from lyra.sdk.models.observability import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    LivenessResponse,
    QueuesResponse,
    ReadinessResponse,
    WorkerDetail,
    WorkersResponse,
)
from pydantic import TypeAdapter, ValidationError

_ResponseT = TypeVar("_ResponseT")


@dataclass(frozen=True)
class RequestSpec(Generic[_ResponseT]):
    """A JSON operation, including its response contract."""

    method: str
    path: str
    operation: str
    adapter: TypeAdapter[_ResponseT]
    authenticated: bool = True
    accepted_statuses: tuple[int, ...] = (200,)
    params: dict[str, Any] | None = None
    json_body: dict[str, Any] | None = None
    require_json: bool = False


def check_status(
    status: int,
    text: str,
    retry_after: str | None,
    *,
    operation: str,
    accepted_statuses: tuple[int, ...] = (200,),
) -> None:
    """Validate HTTP status, raising ServiceUnavailableError for structured 503s.

    Raises:
        DownloadError: If the status or error body is unexpected.
    """
    if status in accepted_statuses:
        return
    err = f"Failed to {operation}. HTTP {status}: {text}"
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    unavailable = service_unavailable_error(payload, retry_after)
    if unavailable is not None:
        unavailable.retryable = unavailable.retryable and status in {
            429,
            500,
            502,
            503,
            504,
        }
        raise unavailable
    raise DownloadError(
        err,
        retryable=status in {429, 500, 502, 503, 504},
        retry_after_seconds=parse_retry_after(retry_after),
    )


def decode_response(
    spec: RequestSpec[_ResponseT],
    text: str,
) -> _ResponseT:
    """Decode and validate a response body with operation context.

    Returns:
        The typed response.

    Raises:
        DownloadError: If JSON decoding or model validation fails.
    """
    try:
        return spec.adapter.validate_json(text)
    except ValidationError as exc:
        err = f"Failed to {spec.operation}: invalid JSON response."
        raise DownloadError(err) from exc


def _validate_response(
    spec: RequestSpec[_ResponseT],
    status: int,
    text: str,
    content_type: str,
    retry_after: str | None,
) -> _ResponseT:
    """Apply the endpoint's status, content type, and response contract.

    Returns:
        The typed response.

    Raises:
        DownloadError: If a result response was not JSON.
    """
    check_status(
        status,
        text,
        retry_after,
        operation=spec.operation,
        accepted_statuses=spec.accepted_statuses,
    )
    if spec.require_json and "application/json" not in content_type:
        err = f"Failed to {spec.operation}: response was not JSON."
        raise DownloadError(err)
    return decode_response(spec, text)


def validate_response(
    spec: RequestSpec[_ResponseT],
    status: int,
    text: str,
    content_type: str,
    retry_after: str | None,
) -> _ResponseT:
    """Validate a response and explain potentially accepted submissions.

    Returns:
        The typed response.

    Raises:
        DownloadError: If the transport response is unsuccessful or invalid.
        ServiceUnavailableError: If the server reports structured unavailability.
    """
    try:
        return _validate_response(spec, status, text, content_type, retry_after)
    except (DownloadError, ServiceUnavailableError) as exc:
        if (
            spec.method == "POST"
            and spec.path == "jobs"
            and (status >= 500 or status in {202, 408})
        ):
            exc.args = (
                (
                    f"{exc} The job may have been accepted; "
                    "another submission may duplicate it."
                ),
            )
        raise


def get_liveness() -> RequestSpec[LivenessResponse]:
    """Return the request specification to fetch liveness."""
    return RequestSpec(
        "GET",
        "live",
        "fetch liveness",
        TypeAdapter(LivenessResponse),
        authenticated=False,
    )


def get_readiness() -> RequestSpec[ReadinessResponse]:
    """Return the request specification to fetch readiness."""
    return RequestSpec(
        "GET",
        "ready",
        "fetch readiness",
        TypeAdapter(ReadinessResponse),
        authenticated=False,
        accepted_statuses=(200, 503),
    )


def get_met_zone_code(name: str) -> RequestSpec[MetZoneCodeResponse]:
    """Return the request specification to fetch met-zone lookup."""
    return RequestSpec(
        "GET",
        "lookups/met-zones",
        "fetch met-zone lookup",
        TypeAdapter(MetZoneCodeResponse),
        authenticated=False,
        params={"name": name},
    )


def create_job(metric: str, payload: dict[str, Any]) -> RequestSpec[JobCreateResponse]:
    """Return the request specification to create job."""
    body: dict[str, Any] = {"metric": metric, "input": payload}
    return RequestSpec(
        "POST",
        "jobs",
        "create job",
        TypeAdapter(JobCreateResponse),
        accepted_statuses=(202,),
        json_body=body,
    )


def get_job(job_id: str) -> RequestSpec[JobStatusInfo]:
    """Return the request specification to fetch job."""
    return RequestSpec("GET", f"jobs/{job_id}", "fetch job", TypeAdapter(JobStatusInfo))


def list_admin_jobs(
    *,
    limit: int = 50,
    status: JobLifecycleStatus,
    queue: str,
    offset: int = 0,
) -> RequestSpec[JobListResponse]:
    """Return the request specification to list admin jobs."""
    params: dict[str, Any] = {"limit": limit}
    params.update(status=status, queue=queue, offset=offset)
    return RequestSpec(
        "GET",
        "admin/jobs",
        "list admin jobs",
        TypeAdapter(JobListResponse),
        params=params,
    )


def get_admin_job(job_id: str) -> RequestSpec[AdminJobDetail]:
    """Return the request specification to fetch admin job."""
    return RequestSpec(
        "GET",
        f"admin/jobs/{job_id}",
        "fetch admin job",
        TypeAdapter(AdminJobDetail),
    )


def list_plugins() -> RequestSpec[InstalledPluginListResponse]:
    """Return the request specification to list installed plugins."""
    return RequestSpec(
        "GET",
        "admin/plugins",
        "list installed plugins",
        TypeAdapter(InstalledPluginListResponse),
    )


def list_plugin_routing() -> RequestSpec[PluginRoutingResponse]:
    """Return the request specification to list plugin routing."""
    return RequestSpec(
        "GET",
        "admin/plugin-routing",
        "list plugin routing",
        TypeAdapter(PluginRoutingResponse),
    )


def get_admin_status() -> RequestSpec[AdminStatusResponse]:
    """Return the request specification to fetch admin status."""
    return RequestSpec(
        "GET", "admin/status", "fetch admin status", TypeAdapter(AdminStatusResponse)
    )


def get_admin_config_summary() -> RequestSpec[ConfigSummaryResponse]:
    """Return the request specification to fetch admin config summary."""
    return RequestSpec(
        "GET",
        "admin/config-summary",
        "fetch admin config summary",
        TypeAdapter(ConfigSummaryResponse),
    )


def get_admin_catalog() -> RequestSpec[CatalogSummaryResponse]:
    """Return the request specification to fetch admin catalog."""
    return RequestSpec(
        "GET",
        "admin/catalog",
        "fetch admin catalog",
        TypeAdapter(CatalogSummaryResponse),
    )


def get_admin_workers() -> RequestSpec[WorkersResponse]:
    """Return the request specification to fetch admin workers."""
    return RequestSpec(
        "GET", "admin/workers", "fetch admin workers", TypeAdapter(WorkersResponse)
    )


def get_admin_worker(worker_name: str) -> RequestSpec[WorkerDetail]:
    """Return the request specification to fetch admin worker."""
    return RequestSpec(
        "GET",
        f"admin/workers/{worker_name}",
        "fetch admin worker",
        TypeAdapter(WorkerDetail),
    )


def get_admin_queues() -> RequestSpec[QueuesResponse]:
    """Return the request specification to fetch admin queues."""
    return RequestSpec(
        "GET", "admin/queues", "fetch admin queues", TypeAdapter(QueuesResponse)
    )


def get_job_result(job_id: str) -> RequestSpec[TerminalJobResult]:
    """Return the request specification to fetch job result."""
    return RequestSpec(
        "GET",
        f"jobs/{job_id}/result",
        "fetch job result",
        TypeAdapter(TerminalJobResult),
        require_json=True,
    )


def get_result_descriptor(job_id: str) -> RequestSpec[ResultDescriptor]:
    """Return the request specification to fetch result descriptor."""
    return RequestSpec(
        "GET",
        f"jobs/{job_id}/result/descriptor",
        "fetch result descriptor",
        TypeAdapter(ResultDescriptor),
    )


def get_data_types() -> RequestSpec[DataTypesResponse]:
    """Return the request specification to fetch data types."""
    return RequestSpec(
        "GET",
        "data-types",
        "fetch data types",
        TypeAdapter(DataTypesResponse),
        authenticated=False,
    )


def get_metrics() -> RequestSpec[MetricCatalogResponse]:
    """Return the request specification to fetch metrics."""
    return RequestSpec(
        "GET",
        "metrics",
        "fetch metrics",
        TypeAdapter(MetricCatalogResponse),
        authenticated=False,
    )


def get_metric(metric_name: str) -> RequestSpec[MetricInfo]:
    """Return the request specification to fetch metric."""
    return RequestSpec(
        "GET",
        f"metrics/{metric_name}",
        "fetch metric",
        TypeAdapter(MetricInfo),
        authenticated=False,
    )
