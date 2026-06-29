import re
from typing import Annotated, Any, Literal, Self

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
    """Package-level metadata declared by a Lyra runner plugin."""

    name: str = Field(min_length=1, description="Human-readable plugin name.")
    version: str = Field(min_length=1, description="Plugin package version.")


class MetricExecutionV2(StrictBaseModel):
    """Execution routing metadata for a plugin metric."""

    queue: str = Field(min_length=1, description="Celery queue used by this metric.")


SpatialInputKind = Literal["location", "bounds"]
OutputColumnType = Literal["number", "integer", "string", "boolean"]


class TableOutputColumnV2(StrictBaseModel):
    """One scalar column produced by a table metric."""

    name: str = Field(min_length=1, description="Column name in the result table.")
    type: OutputColumnType = Field(description="Scalar value type for this column.")
    unit: str = Field(min_length=1, description="Measurement unit for this column.")
    description: str = Field(
        min_length=1,
        description="Human-readable column description.",
    )
    nullable: bool = Field(
        default=False,
        description="Whether this column may contain null values.",
    )


class TableMetricOutputV2(StrictBaseModel):
    """Output declaration for per-feature value metrics."""

    kind: Literal["table"] = Field(description="Metric output kind.")
    columns: list[TableOutputColumnV2] = Field(
        min_length=1,
        description="Ordered result columns.",
    )

    @model_validator(mode="after")
    def validate_unique_columns(self) -> Self:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for column in self.columns:
            if column.name in seen:
                duplicates.add(column.name)
            seen.add(column.name)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            msg = f"duplicate table output column name(s): {names}"
            raise ValueError(msg)
        return self


class FileMetricOutputV2(StrictBaseModel):
    """Output declaration for file-producing metrics."""

    kind: Literal["file"] = Field(description="Metric output kind.")
    media_type: str = Field(min_length=1, description="Produced file media type.")
    extensions: list[str] = Field(
        min_length=1,
        description="Allowed file extensions, including the leading dot.",
    )

    @field_validator("extensions")
    @classmethod
    def validate_extensions(cls, extensions: list[str]) -> list[str]:
        invalid = [
            extension
            for extension in extensions
            if not extension.startswith(".") or len(extension) == 1
        ]
        if invalid:
            names = ", ".join(sorted(invalid))
            msg = (
                "file output extension(s) must start with '.' and include a suffix: "
                f"{names}"
            )
            raise ValueError(msg)

        lowered = [extension.lower() for extension in extensions]
        if len(set(lowered)) != len(lowered):
            msg = "file output extensions must be unique"
            raise ValueError(msg)
        return extensions


MetricOutputV2 = Annotated[
    TableMetricOutputV2 | FileMetricOutputV2,
    Field(discriminator="kind"),
]


class MetricManifestV2(StrictBaseModel):
    """Manifest entry that describes one executable metric."""

    name: str = Field(min_length=1, description="Public metric name.")
    description: str = Field(
        min_length=1,
        description="Short description shown to API clients.",
    )
    request_schema: dict[str, Any] = Field(
        description="JSON Schema for the unresolved client request payload.",
    )
    output: MetricOutputV2 = Field(
        description="Successful metric output declaration.",
    )
    spatial_inputs: dict[str, SpatialInputKind] = Field(
        min_length=1,
        description="Request fields that Lyra resolves into spatial GeoJSON inputs.",
    )
    execution: MetricExecutionV2 = Field(description="Queue routing metadata.")
    entrypoint: str = Field(description="Python module:function runner reference.")

    @field_validator("request_schema")
    @classmethod
    def validate_request_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        _validate_json_schema(schema, "request_schema")
        return schema

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, entrypoint: str) -> str:
        if not _ENTRYPOINT_PATTERN.fullmatch(entrypoint):
            msg = "entrypoint must be a module:function reference"
            raise ValueError(msg)
        return entrypoint

    @field_validator("spatial_inputs")
    @classmethod
    def validate_spatial_inputs(
        cls,
        spatial_inputs: dict[str, SpatialInputKind],
    ) -> dict[str, SpatialInputKind]:
        empty_fields = [field for field in spatial_inputs if not field.strip()]
        if empty_fields:
            msg = "spatial_inputs field names must be non-empty strings"
            raise ValueError(msg)
        return spatial_inputs

    @model_validator(mode="after")
    def validate_spatial_request_schema(self) -> Self:
        request_schema = self.request_schema
        if request_schema.get("type") != "object":
            msg = "request_schema must be an object schema for spatial metrics"
            raise ValueError(msg)

        properties = request_schema.get("properties")
        if not isinstance(properties, dict):
            msg = "request_schema must define object properties"
            raise TypeError(msg)

        required = request_schema.get("required")
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            msg = "request_schema must define required as a list of strings"
            raise TypeError(msg)

        missing_properties = sorted(
            field for field in self.spatial_inputs if field not in properties
        )
        if missing_properties:
            names = ", ".join(missing_properties)
            msg = (
                "spatial input field(s) missing from request_schema.properties: "
                f"{names}"
            )
            raise ValueError(msg)

        required_fields = set(required)
        optional_spatial_fields = sorted(
            field for field in self.spatial_inputs if field not in required_fields
        )
        if optional_spatial_fields:
            names = ", ".join(optional_spatial_fields)
            msg = (
                f"spatial input field(s) missing from request_schema.required: {names}"
            )
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def validate_output_spatial_contract(self) -> Self:
        if (
            isinstance(self.output, TableMetricOutputV2)
            and self.spatial_inputs.get("location") != "location"
        ):
            msg = "table metrics must declare a location spatial input named 'location'"
            raise ValueError(msg)
        return self


class PluginManifestV2(StrictBaseModel):
    """Top-level v2 plugin manifest file."""

    schema_version: Literal[2] = Field(description="Manifest schema version.")
    plugin: PluginInfoV2 = Field(description="Plugin metadata.")
    metrics: list[MetricManifestV2] = Field(
        min_length=1,
        description="Executable metrics exposed by the plugin.",
    )

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
    "FileMetricOutputV2",
    "MetricExecutionV2",
    "MetricManifestV2",
    "MetricOutputV2",
    "OutputColumnType",
    "PluginInfoV2",
    "PluginManifestV2",
    "SpatialInputKind",
    "TableMetricOutputV2",
    "TableOutputColumnV2",
]
