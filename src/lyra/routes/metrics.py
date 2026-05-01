from fastapi import APIRouter, HTTPException

from lyra.registry import MetricInfo, get_metric_info, get_metrics_info

router = APIRouter()


@router.get("/metrics", response_model=list[MetricInfo])
@router.get("/metrics/{metric_name}", response_model=MetricInfo)
async def list_metrics(metric_name: str | None = None) -> list[MetricInfo] | MetricInfo:
    if metric_name is None:
        return get_metrics_info()
    info = get_metric_info(metric_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Metric '{metric_name}' not found.",
        )
    return info
