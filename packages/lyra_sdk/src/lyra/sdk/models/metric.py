from typing import Any

from lyra.sdk.models.strict import StrictBaseModel


class MetricParameterInfo(StrictBaseModel):
    name: str
    type: str
    required: bool
    default: Any | None


class MetricInfo(StrictBaseModel):
    name: str
    description: str
    parameters: list[MetricParameterInfo]
    returns_file: bool
    tavi_hint: str
