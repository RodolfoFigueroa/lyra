import re
from typing import Any, Literal, Self

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field, field_validator, model_validator

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_ENTRYPOINT_PATTERN = re.compile(
    rf"^{_IDENTIFIER}(?:\.{_IDENTIFIER})*:{_IDENTIFIER}$",
)


def _validate_json_schema(schema: dict[str, Any], field_name: str) -> None:
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        msg = f"invalid {field_name}: {exc.message}"
        raise ValueError(msg) from exc


class PluginInfoV2(StrictBaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class MetricExecutionV2(StrictBaseModel):
    queue: str = Field(min_length=1)


class MetricManifestV2(StrictBaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    request_schema: dict[str, Any]
    result_schema: dict[str, Any] | None = None
    execution: MetricExecutionV2
    entrypoint: str

    @field_validator("request_schema")
    @classmethod
    def validate_request_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        _validate_json_schema(schema, "request_schema")
        return schema

    @field_validator("result_schema")
    @classmethod
    def validate_result_schema(
        cls,
        schema: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if schema is not None:
            _validate_json_schema(schema, "result_schema")
        return schema

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, entrypoint: str) -> str:
        if not _ENTRYPOINT_PATTERN.fullmatch(entrypoint):
            msg = "entrypoint must be a module:function reference"
            raise ValueError(msg)
        return entrypoint


class PluginManifestV2(StrictBaseModel):
    schema_version: Literal[2]
    plugin: PluginInfoV2
    metrics: list[MetricManifestV2] = Field(min_length=1)

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
    "MetricExecutionV2",
    "MetricManifestV2",
    "PluginInfoV2",
    "PluginManifestV2",
]
