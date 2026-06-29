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
_SCALAR_JSON_SCHEMA_TYPES = {"boolean", "integer", "number", "string"}


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


class BatchedTableOutputColumnV2(StrictBaseModel):
    """Column group generated from a bounded input array."""

    source: str = Field(
        min_length=1,
        description="Top-level request array field used to generate columns.",
    )
    name_template: str = Field(
        min_length=1,
        description="Column name template containing '{value}'.",
    )
    type: OutputColumnType = Field(description="Scalar value type for these columns.")
    unit: str = Field(min_length=1, description="Measurement unit for these columns.")
    description_template: str = Field(
        min_length=1,
        description="Column description template containing '{value}'.",
    )
    nullable: bool = Field(
        default=False,
        description="Whether these columns may contain null values.",
    )
    batching_reason: str = Field(
        min_length=1,
        description="Why one job can reuse work across source values.",
    )

    @field_validator("name_template")
    @classmethod
    def validate_name_template(cls, template: str) -> str:
        if "{value}" not in template:
            msg = "name_template must contain '{value}'"
            raise ValueError(msg)
        return template

    @field_validator("description_template")
    @classmethod
    def validate_description_template(cls, template: str) -> str:
        if "{value}" not in template:
            msg = "description_template must contain '{value}'"
            raise ValueError(msg)
        return template

    @field_validator("batching_reason")
    @classmethod
    def validate_batching_reason(cls, reason: str) -> str:
        if not reason.strip():
            msg = "batching_reason must be non-empty"
            raise ValueError(msg)
        return reason


class TableMetricOutputV2(StrictBaseModel):
    """Output declaration for per-feature value metrics."""

    kind: Literal["table"] = Field(description="Metric output kind.")
    columns: list[TableOutputColumnV2] = Field(
        default_factory=list,
        description="Ordered static result columns.",
    )
    batched_columns: list[BatchedTableOutputColumnV2] = Field(
        default_factory=list,
        description="Ordered input-array-backed result column groups.",
    )

    @model_validator(mode="after")
    def validate_columns(self) -> Self:
        if not self.columns and not self.batched_columns:
            msg = "table outputs must declare columns or batched_columns"
            raise ValueError(msg)

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

    @model_validator(mode="after")
    def validate_batched_column_sources(self) -> Self:
        if not isinstance(self.output, TableMetricOutputV2):
            return self

        properties = self.request_schema["properties"]
        required_fields = set(self.request_schema["required"])
        for column in self.output.batched_columns:
            if column.source not in properties:
                msg = (
                    "batched column source field missing from "
                    f"request_schema.properties: {column.source}"
                )
                raise ValueError(msg)

            if column.source not in required_fields:
                msg = (
                    "batched column source field missing from "
                    f"request_schema.required: {column.source}"
                )
                raise ValueError(msg)

            source_schema = properties[column.source]
            if not isinstance(source_schema, dict):
                msg = (
                    f"batched column source {column.source!r} must be an object schema"
                )
                raise TypeError(msg)

            if source_schema.get("type") != "array":
                msg = f"batched column source {column.source!r} must be an array"
                raise ValueError(msg)

            min_items = source_schema.get("minItems")
            if (
                not isinstance(min_items, int)
                or isinstance(min_items, bool)
                or min_items < 1
            ):
                msg = (
                    f"batched column source {column.source!r} must declare "
                    "minItems >= 1"
                )
                raise ValueError(msg)

            max_items = source_schema.get("maxItems")
            if (
                not isinstance(max_items, int)
                or isinstance(max_items, bool)
                or max_items < 1
            ):
                msg = (
                    f"batched column source {column.source!r} must declare "
                    "maxItems >= 1"
                )
                raise ValueError(msg)

            if source_schema.get("uniqueItems") is not True:
                msg = (
                    f"batched column source {column.source!r} must declare "
                    "uniqueItems: true"
                )
                raise ValueError(msg)

            items_schema = source_schema.get("items")
            if not isinstance(items_schema, dict):
                msg = f"batched column source {column.source!r} must declare items"
                raise TypeError(msg)

            item_type = items_schema.get("type")
            if item_type not in _SCALAR_JSON_SCHEMA_TYPES:
                msg = (
                    f"batched column source {column.source!r} items must be "
                    "string, integer, number, or boolean"
                )
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
    "BatchedTableOutputColumnV2",
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
