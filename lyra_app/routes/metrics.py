"""HTTP endpoints for metric discovery and metadata."""

from fastapi import APIRouter, HTTPException, Response
from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfoV4

from lyra_app.registry import get_metric_catalog, get_metric_info

router = APIRouter(tags=["Catalog"])


@router.get("/metrics")
async def list_metrics(response: Response) -> MetricCatalogResponse:
    """Return the public metric catalog and expose its fingerprint as an ETag.

    Returns:
        The versioned public metric catalog.
    """
    catalog = get_metric_catalog()
    response.headers["ETag"] = catalog.catalog_fingerprint
    return catalog


@router.get("/metrics/{metric_name}")
async def get_metric(metric_name: str) -> MetricInfoV4:
    """Return public metadata for one registered metric.

    Returns:
        The metric's request, spatial-input, and output contracts.

    Raises:
        HTTPException: If the active catalog has no metric with that name.
    """
    info = get_metric_info(metric_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Metric '{metric_name}' not found.",
        )
    return info
