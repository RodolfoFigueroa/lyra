from fastapi import APIRouter

from lyra.registry import MetricInfo, get_metrics_info

router = APIRouter()


@router.get("/metrics", response_model=list[MetricInfo])
async def list_metrics() -> list[MetricInfo]:
    return get_metrics_info()
