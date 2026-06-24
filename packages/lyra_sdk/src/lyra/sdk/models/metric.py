from typing import Any

from lyra.sdk.models.strict import StrictBaseModel


class MetricInfoV2(StrictBaseModel):
    name: str
    description: str
    request_schema: dict[str, Any]
    result_schema: dict[str, Any] | None = None
