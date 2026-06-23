from typing import Any, Literal, Self

from lyra.sdk.models.metric import MetricParameterInfo
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field, model_validator


class PluginInfo(StrictBaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class MetricExecution(StrictBaseModel):
    profile: str = Field(min_length=1)
    queue: str = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, gt=0)


class MetricCallable(StrictBaseModel):
    mode: Literal["single", "batched"]
    calculate: str | None = None
    prepare: str | None = None
    for_items: str | None = None
    aggregate: str | None = None
    items_default: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> Self:
        if self.mode == "single":
            if not self.calculate:
                msg = "single metrics must define callable.calculate"
                raise ValueError(msg)
            return self

        missing = [
            name
            for name in ("prepare", "for_items", "aggregate")
            if getattr(self, name) is None
        ]
        if missing:
            msg = f"batched metrics are missing callable field(s): {', '.join(missing)}"
            raise ValueError(msg)
        return self


class MetricManifest(StrictBaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: list[MetricParameterInfo] = Field(default_factory=list)
    returns_file: bool = False
    tavi_hint: str = ""
    request_schema: dict[str, Any] = Field(default_factory=dict)
    execution: MetricExecution
    callable: MetricCallable


class PluginManifest(StrictBaseModel):
    schema_version: Literal[1]
    plugin: PluginInfo
    metrics: list[MetricManifest] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_metric_names(self) -> Self:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for metric in self.metrics:
            if metric.name in seen:
                duplicates.add(metric.name)
            seen.add(metric.name)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            msg = f"duplicate metric name(s) in plugin manifest: {names}"
            raise ValueError(msg)
        return self


__all__ = [
    "MetricCallable",
    "MetricExecution",
    "MetricManifest",
    "PluginInfo",
    "PluginManifest",
]
