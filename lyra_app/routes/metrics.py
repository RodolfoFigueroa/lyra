from fastapi import APIRouter, HTTPException
from lyra.sdk.models.metric import MetricInfoV3

from lyra_app.registry import get_metric_info, get_metrics_info

router = APIRouter()


@router.get("/metrics", response_model=list[MetricInfoV3])
@router.get("/metrics/{metric_name}", response_model=MetricInfoV3)
async def list_metrics(
    metric_name: str | None = None,
) -> list[MetricInfoV3] | MetricInfoV3:
    if metric_name is None:
        return get_metrics_info()
    info = get_metric_info(metric_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Metric '{metric_name}' not found.",
        )
    return info
