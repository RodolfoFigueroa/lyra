from fastapi import APIRouter, HTTPException

from lyra_app.registry import MetricInfo, get_metric_info, get_metrics_info

router = APIRouter()


@router.get("/metrics", response_model=list[MetricInfo])
@router.get("/metrics/{metric_name}", response_model=MetricInfo)
async def list_metrics(
    metric_name: str | None = None, *, prettify_types: bool = True
) -> list[MetricInfo] | MetricInfo:
    if metric_name is None:
        return get_metrics_info(prettify_types=prettify_types)
    info = get_metric_info(metric_name, prettify_types=prettify_types)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Metric '{metric_name}' not found.",
        )
    return info
