import json
import math
import re
from copy import deepcopy
from typing import Annotated, Any, Literal, Self

from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from jsonschema.validators import validator_for
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field, TypeAdapter, field_validator, model_validator

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_ENTRYPOINT_PATTERN = re.compile(
    rf"^{_IDENTIFIER}(?:\.{_IDENTIFIER})*:{_IDENTIFIER}$",
)
_TEMPLATE_FIELD_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BATCH_KEY_SCHEMA = {
    "type": "string",
    "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
    "minLength": 1,
    "maxLength": 64,
    "description": "Stable identifier for this batch item.",
}
_BATCH_LABEL_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 120,
    "description": "Optional human-readable label for this batch item.",
}
_LOCATION_DESCRIPTION = (
    "Locations to analyze, supplied through a supported spatial reference format."
)
_LOCATION_EXAMPLES = [
    {"data_type": "cvegeo_list", "value": ["09002"]},
]
_BOUNDS_DESCRIPTION = (
    "Area used to bound the analysis, supplied through a supported spatial "
    "reference format."
)
_BOUNDS_EXAMPLES = [
    {"data_type": "met_zone_code", "value": "ZMVM"},
]
_BATCHED_ITEM_FIELDS = {"key", "value", "label"}
_BATCHED_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_json_schema(schema: dict[str, Any], field_name: str) -> None:
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        msg = f"invalid {field_name}: {exc.message}"
        raise ValueError(msg) from exc


def _template_fields(template: str) -> set[str]:
    return set(_TEMPLATE_FIELD_PATTERN.findall(template))


def _json_scalar_identity(value: Any) -> str:
    return f"{type(value).__name__}:{json.dumps(value, sort_keys=True)}"


def _const_to_enum(value: Any) -> Any:
    if isinstance(value, dict):
        converted = {
            key: _const_to_enum(item) for key, item in value.items() if key != "const"
        }
        if "const" in value:
            converted["enum"] = [deepcopy(value["const"])]
        return converted
    if isinstance(value, list):
        return [_const_to_enum(item) for item in value]
    return deepcopy(value)


class CVEGEOListWrapperV3(StrictBaseModel):
    data_type: Literal["cvegeo_list"]
    value: list[str]


class GeoJSONLocationWrapperV3(StrictBaseModel):
    data_type: Literal["geojson"]
    value: GeoJSON


class GeoJSONBoundsWrapperV3(StrictBaseModel):
    data_type: Literal["geojson"]
    value: SingleGeoJSON


class MetZoneCodeWrapperV3(StrictBaseModel):
    data_type: Literal["met_zone_code"]
    value: str = Field(min_length=1)


_LocationWrapperUnionV3 = Annotated[
    CVEGEOListWrapperV3 | GeoJSONLocationWrapperV3 | MetZoneCodeWrapperV3,
    Field(discriminator="data_type"),
]
_BoundsWrapperUnionV3 = Annotated[
    CVEGEOListWrapperV3 | GeoJSONBoundsWrapperV3 | MetZoneCodeWrapperV3,
    Field(discriminator="data_type"),
]
_LOCATION_WRAPPER_ADAPTER = TypeAdapter(_LocationWrapperUnionV3)
_BOUNDS_WRAPPER_ADAPTER = TypeAdapter(_BoundsWrapperUnionV3)


class PluginInfoV3(StrictBaseModel):
    """Package-level metadata declared by a schema v3 Lyra plugin."""

    name: str = Field(min_length=1, description="Human-readable plugin name.")
    version: str = Field(min_length=1, description="Plugin package version.")


class PluginOwnedInputMetadataV3(StrictBaseModel):
    """Metadata accepted only by plugin-owned schema v3 input values."""

    description: str | None = Field(
        default=None,
        min_length=1,
        description="Human-readable input description.",
    )
    default: Any = Field(
        default=None,
        description="Default value applied by clients when omitted.",
    )
    examples: list[Any] | None = Field(
        default=None,
        description="Example values for this input.",
    )
    required: bool = Field(
        default=True,
        description="Whether the root request must contain this input.",
    )
    nullable: bool = Field(
        default=False,
        description="Whether this input accepts explicit null values.",
    )


class LocationInputV3(StrictBaseModel):
    """Lyra-owned location spatial input."""

    kind: Literal["location"] = Field(description="Input kind.")


class BoundsInputV3(StrictBaseModel):
    """Lyra-owned bounds spatial input."""

    kind: Literal["bounds"] = Field(description="Input kind.")


SpatialInputKindV3 = Literal["location", "bounds"]


class StringInputV3(PluginOwnedInputMetadataV3):
    """Plugin-owned string input."""

    kind: Literal["string"] = Field(description="Input kind.")
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)
    pattern: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_length_bounds(self) -> Self:
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            msg = "min_length must be less than or equal to max_length"
            raise ValueError(msg)
        return self


class NumberInputV3(PluginOwnedInputMetadataV3):
    """Plugin-owned numeric input."""

    kind: Literal["number"] = Field(description="Input kind.")
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_numeric_bounds(self) -> Self:
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            msg = "minimum must be less than or equal to maximum"
            raise ValueError(msg)
        return self


class IntegerInputV3(PluginOwnedInputMetadataV3):
    """Plugin-owned integer input."""

    kind: Literal["integer"] = Field(description="Input kind.")
    minimum: int | None = None
    maximum: int | None = None

    @model_validator(mode="after")
    def validate_integer_bounds(self) -> Self:
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            msg = "minimum must be less than or equal to maximum"
            raise ValueError(msg)
        return self


class BooleanInputV3(PluginOwnedInputMetadataV3):
    """Plugin-owned boolean input."""

    kind: Literal["boolean"] = Field(description="Input kind.")


class EnumInputV3(PluginOwnedInputMetadataV3):
    """Plugin-owned enum input."""

    kind: Literal["enum"] = Field(description="Input kind.")
    values: list[Any] = Field(min_length=1, description="Allowed scalar values.")

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: list[Any]) -> list[Any]:
        identities: set[str] = set()
        duplicates: list[Any] = []
        for value in values:
            if value is None:
                msg = "enum values must not include null; use nullable: true"
                raise ValueError(msg)
            if isinstance(value, float) and not math.isfinite(value):
                msg = "enum values must be finite JSON scalar values"
                raise ValueError(msg)
            if not isinstance(value, str | int | float | bool):
                msg = "enum values must be JSON scalar values"
                raise ValueError(msg)  # noqa: TRY004

            identity = _json_scalar_identity(value)
            if identity in identities:
                duplicates.append(value)
            identities.add(identity)

        if duplicates:
            duplicate_names = ", ".join(json.dumps(value) for value in duplicates)
            msg = f"enum values must be unique: {duplicate_names}"
            raise ValueError(msg)
        return values


class JsonSchemaInputV3(PluginOwnedInputMetadataV3):
    """Plugin-owned raw JSON Schema input."""

    kind: Literal["json_schema"] = Field(description="Input kind.")
    schema_: dict[str, Any] = Field(
        alias="schema",
        description="Plugin-owned JSON Schema.",
    )

    @property
    def schema(self) -> dict[str, Any]:
        return self.schema_

    @field_validator("schema_")
    @classmethod
    def validate_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        _validate_json_schema(schema, "json_schema.schema")
        return schema


PluginOwnedInputSpecV3 = Annotated[
    StringInputV3
    | NumberInputV3
    | IntegerInputV3
    | BooleanInputV3
    | EnumInputV3
    | JsonSchemaInputV3,
    Field(discriminator="kind"),
]


class BatchInputV3(StrictBaseModel):
    """Metric-local batch input that can drive dynamic table columns."""

    kind: Literal["batch"] = Field(description="Input kind.")
    max_items: int = Field(ge=1, description="Maximum number of batch items.")
    value: PluginOwnedInputSpecV3 = Field(
        description="Plugin-owned schema for each batch item value.",
    )
    label: bool = Field(
        default=False,
        description="Whether clients may submit display labels for items.",
    )


InputSpecV3 = Annotated[
    LocationInputV3 | BoundsInputV3 | BatchInputV3 | PluginOwnedInputSpecV3,
    Field(discriminator="kind"),
]

OutputColumnTypeV3 = Literal["number", "integer", "string", "boolean"]


class FractionOfLocationAreaDerivationV3(StrictBaseModel):
    """Server-owned fraction derived from a square-metre result column."""

    kind: Literal["fraction_of_location_area"] = Field(
        description="Derived output operation.",
    )
    name: str = Field(min_length=1, description="Derived result column name.")
    description: str = Field(
        min_length=1,
        description="Human-readable derived column description.",
    )


class TableOutputColumnV3(StrictBaseModel):
    """One scalar column produced by a schema v3 table metric."""

    name: str = Field(min_length=1, description="Column name in the result table.")
    type: OutputColumnTypeV3 = Field(description="Scalar value type for this column.")
    unit: str = Field(min_length=1, description="Measurement unit for this column.")
    description: str = Field(
        min_length=1,
        description="Human-readable column description.",
    )
    nullable: bool = Field(
        default=False,
        description="Whether this column may contain null values.",
    )
    derivations: list[FractionOfLocationAreaDerivationV3] = Field(
        default_factory=list,
        max_length=1,
        description="Server-owned columns derived from this runner output column.",
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def validate_derivations(self) -> Self:
        if not self.derivations:
            return self
        if self.type not in {"number", "integer"}:
            msg = "fractional-area derivations require a numeric source column"
            raise ValueError(msg)
        if self.unit != "m2":
            msg = "fractional-area derivations require source unit 'm2'"
            raise ValueError(msg)
        return self


class BatchedTableOutputColumnV3(StrictBaseModel):
    """Column group generated from a schema v3 batch input."""

    source: str = Field(
        min_length=1,
        description="Metric-local batch input used to generate columns.",
    )
    name: str = Field(
        min_length=1,
        description="Column name template containing '{key}'.",
    )
    type: OutputColumnTypeV3 = Field(description="Scalar value type for these columns.")
    unit: str = Field(min_length=1, description="Measurement unit for these columns.")
    description: str = Field(
        min_length=1,
        description="Column description template using '{key}' and/or '{label}'.",
    )
    nullable: bool = Field(
        default=False,
        description="Whether these columns may contain null values.",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, template: str) -> str:
        fields = _template_fields(template)
        invalid_fields = sorted(fields - {"key"})
        if invalid_fields:
            names = ", ".join(invalid_fields)
            msg = f"batched column name contains unsupported field(s): {names}"
            raise ValueError(msg)
        if "key" not in fields:
            msg = "batched column name must contain '{key}'"
            raise ValueError(msg)
        return template

    @field_validator("description")
    @classmethod
    def validate_description(cls, template: str) -> str:
        fields = _template_fields(template)
        invalid_fields = sorted(fields - {"key", "label"})
        if invalid_fields:
            names = ", ".join(invalid_fields)
            msg = f"batched column description contains unsupported field(s): {names}"
            raise ValueError(msg)
        return template


class TableOutputV3(StrictBaseModel):
    """Output declaration for schema v3 table metrics."""

    kind: Literal["table"] = Field(description="Metric output kind.")
    columns: list[TableOutputColumnV3] = Field(
        default_factory=list,
        description="Ordered static result columns.",
    )
    batched_columns: list[BatchedTableOutputColumnV3] = Field(
        default_factory=list,
        description="Ordered batch-backed result column groups.",
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
            for derivation in column.derivations:
                if derivation.name in seen:
                    duplicates.add(derivation.name)
                seen.add(derivation.name)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            msg = f"duplicate table output column name(s): {names}"
            raise ValueError(msg)
        return self


def _batched_template_context(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        msg = "Batched column source values must be objects."
        raise TypeError(msg)

    invalid_fields = sorted(set(value) - _BATCHED_ITEM_FIELDS)
    if invalid_fields:
        names = ", ".join(invalid_fields)
        msg = f"Batched column source values contain unsupported fields: {names}."
        raise ValueError(msg)

    key = value.get("key")
    if not isinstance(key, str) or not _BATCHED_KEY_PATTERN.fullmatch(key):
        msg = (
            "Batched column source value 'key' must be a non-empty string matching "
            "Lyra's batched key pattern."
        )
        raise ValueError(msg)

    if "value" not in value:
        msg = "Batched column source values must contain 'value'."
        raise ValueError(msg)

    label = value.get("label", key)
    if not isinstance(label, str):
        msg = "Batched column source value 'label' must be a string when provided."
        raise TypeError(msg)

    return {"key": key, "label": label}


def _expand_batched_template(template: str, context: dict[str, str]) -> str:
    value = template
    for name, replacement in context.items():
        value = value.replace(f"{{{name}}}", replacement)
    return value


def expand_runner_table_output_columns(
    output: TableOutputV3,
    job_input: dict[str, Any],
) -> list[TableOutputColumnV3]:
    """Expand only columns that a runner must return for one job input."""

    columns = [column.model_copy(deep=True) for column in output.columns]

    for column_group in output.batched_columns:
        source_values = job_input.get(column_group.source)
        if not isinstance(source_values, list):
            msg = (
                f"Batched column source {column_group.source!r} must be present "
                "as an array."
            )
            raise TypeError(msg)

        for source_value in source_values:
            template_context = _batched_template_context(source_value)
            name = _expand_batched_template(column_group.name, template_context)
            if not name:
                msg = "Batched column templates must produce non-empty names."
                raise ValueError(msg)
            description = _expand_batched_template(
                column_group.description,
                template_context,
            )
            columns.append(
                TableOutputColumnV3(
                    name=name,
                    type=column_group.type,
                    unit=column_group.unit,
                    description=description,
                    nullable=column_group.nullable,
                )
            )

    names = [column.name for column in columns]
    if len(names) != len(set(names)):
        msg = "Expanded table output columns must be unique."
        raise ValueError(msg)

    return columns


def expand_table_output_columns(
    output: TableOutputV3,
    job_input: dict[str, Any],
) -> list[TableOutputColumnV3]:
    """Expand the effective table output contract for one validated job input."""

    runner_columns = expand_runner_table_output_columns(output, job_input)
    columns: list[TableOutputColumnV3] = []
    for column in runner_columns:
        columns.append(column)
        columns.extend(
            [
                TableOutputColumnV3(
                    name=derivation.name,
                    type="number",
                    unit="ratio",
                    description=derivation.description,
                    nullable=column.nullable,
                )
                for derivation in column.derivations
            ]
        )

    names = [column.name for column in columns]
    if len(names) != len(set(names)):
        msg = "Expanded table output columns must be unique."
        raise ValueError(msg)

    return columns


class FileOutputV3(StrictBaseModel):
    """Output declaration for schema v3 file-producing metrics."""

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


OutputSpecV3 = Annotated[
    TableOutputV3 | FileOutputV3,
    Field(discriminator="kind"),
]


class MetricManifestV3(StrictBaseModel):
    """Manifest entry that describes one schema v3 executable metric."""

    name: str = Field(min_length=1, description="Public metric name.")
    description: str = Field(
        min_length=1,
        description="Short description shown to API clients.",
    )
    entrypoint: str = Field(description="Python module:function runner reference.")
    inputs: dict[str, InputSpecV3] = Field(
        min_length=1,
        description="Metric request input declarations.",
    )
    output: OutputSpecV3 = Field(description="Successful metric output declaration.")

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, entrypoint: str) -> str:
        if not _ENTRYPOINT_PATTERN.fullmatch(entrypoint):
            msg = "entrypoint must be a module:function reference"
            raise ValueError(msg)
        return entrypoint

    @field_validator("inputs")
    @classmethod
    def validate_input_field_names(
        cls,
        inputs: dict[str, InputSpecV3],
    ) -> dict[str, InputSpecV3]:
        empty_fields = [field for field in inputs if not field.strip()]
        if empty_fields:
            msg = "input field names must be non-empty strings"
            raise ValueError(msg)
        return inputs

    @model_validator(mode="after")
    def validate_spatial_inputs(self) -> Self:
        spatial_inputs = [
            input_spec
            for input_spec in self.inputs.values()
            if isinstance(input_spec, LocationInputV3 | BoundsInputV3)
        ]
        if not spatial_inputs:
            msg = "metrics must declare at least one location or bounds input"
            raise ValueError(msg)

        if isinstance(self.output, TableOutputV3):
            location_input = self.inputs.get("location")
            if not isinstance(location_input, LocationInputV3):
                msg = "table metrics must declare inputs.location as kind 'location'"
                raise ValueError(msg)  # noqa: TRY004
        return self

    @model_validator(mode="after")
    def validate_batch_sources(self) -> Self:
        batch_input_names = {
            name
            for name, input_spec in self.inputs.items()
            if isinstance(input_spec, BatchInputV3)
        }

        if not isinstance(self.output, TableOutputV3):
            if not batch_input_names:
                return self
            names = ", ".join(sorted(batch_input_names))
            msg = f"batch input(s) must be referenced by table batched_columns: {names}"
            raise ValueError(msg)

        for column in self.output.batched_columns:
            source_input = self.inputs.get(column.source)
            if source_input is None:
                msg = f"batched column source is not defined in inputs: {column.source}"
                raise ValueError(msg)
            if not isinstance(source_input, BatchInputV3):
                msg = (
                    "batched column source must reference a batch input: "
                    f"{column.source}"
                )
                raise ValueError(msg)  # noqa: TRY004

        batched_sources = {column.source for column in self.output.batched_columns}
        unreferenced_batches = sorted(batch_input_names - batched_sources)
        if unreferenced_batches:
            names = ", ".join(unreferenced_batches)
            msg = f"batch input(s) must be referenced by batched_columns: {names}"
            raise ValueError(msg)
        return self


class PluginManifestV3(StrictBaseModel):
    """Top-level schema v3 plugin manifest file."""

    schema_version: Literal[3] = Field(description="Manifest schema version.")
    plugin: PluginInfoV3 = Field(description="Plugin metadata.")
    metrics: list[MetricManifestV3] = Field(
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


class CompiledMetricManifestV3(StrictBaseModel):
    """Compiled schema v3 metric contract consumed by Lyra runtime services."""

    name: str = Field(min_length=1, description="Public metric name.")
    description: str = Field(description="Human-readable metric description.")
    entrypoint: str = Field(description="Python module:function runner reference.")
    spatial_inputs: dict[str, SpatialInputKindV3] = Field(
        min_length=1,
        description="Request fields Lyra resolves into spatial GeoJSON inputs.",
    )
    batch_inputs: list[str] = Field(
        description="Request fields Lyra validates as batch inputs.",
    )
    request_schema: dict[str, Any] = Field(
        description="Effective JSON Schema for unresolved client requests.",
    )
    output: OutputSpecV3 = Field(description="Successful metric output declaration.")

    @field_validator("request_schema")
    @classmethod
    def validate_request_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        _validate_json_schema(schema, "request_schema")
        return schema


class CompiledPluginManifestV3(StrictBaseModel):
    """Compiled schema v3 plugin manifest contract."""

    schema_version: Literal[3] = Field(description="Manifest schema version.")
    plugin: PluginInfoV3 = Field(description="Plugin metadata.")
    metrics: list[CompiledMetricManifestV3] = Field(
        min_length=1,
        description="Compiled executable metrics exposed by the plugin.",
    )


def _adapter_for_spatial_kind(kind: SpatialInputKindV3) -> TypeAdapter[Any]:
    return _LOCATION_WRAPPER_ADAPTER if kind == "location" else _BOUNDS_WRAPPER_ADAPTER


def _wrapper_field_schema(
    kind: SpatialInputKindV3,
) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = _const_to_enum(_adapter_for_spatial_kind(kind).json_schema())
    defs = schema.pop("$defs", {})
    if not isinstance(defs, dict):
        msg = f"spatial wrapper schema for {kind!r} did not contain object $defs"
        raise TypeError(msg)
    return schema, defs


def _schema_with_defs(
    schema: dict[str, Any],
    defs: dict[str, Any],
) -> dict[str, Any]:
    if not defs:
        return schema

    schema_with_defs = deepcopy(schema)
    schema_with_defs["$defs"] = deepcopy(defs)
    return schema_with_defs


def _validate_value_against_schema(
    schema: dict[str, Any],
    value: Any,
    path: str,
) -> None:
    validator_cls = validator_for(schema)
    try:
        validator_cls.check_schema(schema)
        validator_cls(schema).validate(value)
    except SchemaError as exc:
        msg = f"{path} compiled schema is invalid: {exc.message}"
        raise ValueError(msg) from exc
    except JSONSchemaValidationError as exc:
        msg = f"{path} must validate against its compiled schema: {exc.message}"
        raise ValueError(msg) from exc


def _apply_common_metadata(
    schema: dict[str, Any],
    input_spec: PluginOwnedInputMetadataV3,
) -> dict[str, Any]:
    compiled = deepcopy(schema)
    if input_spec.nullable:
        compiled = {"anyOf": [compiled, {"type": "null"}]}

    if input_spec.description is not None:
        compiled["description"] = input_spec.description
    if "default" in input_spec.model_fields_set:
        compiled["default"] = deepcopy(input_spec.default)
    if input_spec.examples is not None:
        compiled["examples"] = deepcopy(input_spec.examples)
    return compiled


def _validate_common_values(
    schema: dict[str, Any],
    defs: dict[str, Any],
    input_spec: PluginOwnedInputMetadataV3,
    path: str,
) -> None:
    validation_schema = _schema_with_defs(schema, defs)
    if "default" in input_spec.model_fields_set:
        _validate_value_against_schema(
            validation_schema,
            input_spec.default,
            f"{path}.default",
        )
    if input_spec.examples is not None:
        for index, example in enumerate(input_spec.examples):
            _validate_value_against_schema(
                validation_schema,
                example,
                f"{path}.examples[{index}]",
            )


def _compile_string_input(input_spec: StringInputV3) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if input_spec.min_length is not None:
        schema["minLength"] = input_spec.min_length
    if input_spec.max_length is not None:
        schema["maxLength"] = input_spec.max_length
    if input_spec.pattern is not None:
        schema["pattern"] = input_spec.pattern
    return schema


def _compile_number_input(input_spec: NumberInputV3) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "number"}
    if input_spec.minimum is not None:
        schema["minimum"] = input_spec.minimum
    if input_spec.maximum is not None:
        schema["maximum"] = input_spec.maximum
    return schema


def _compile_integer_input(input_spec: IntegerInputV3) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if input_spec.minimum is not None:
        schema["minimum"] = input_spec.minimum
    if input_spec.maximum is not None:
        schema["maximum"] = input_spec.maximum
    return schema


def _compile_plugin_owned_input(
    input_spec: PluginOwnedInputSpecV3,
    path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    defs: dict[str, Any] = {}
    if isinstance(input_spec, StringInputV3):
        schema = _compile_string_input(input_spec)
    elif isinstance(input_spec, NumberInputV3):
        schema = _compile_number_input(input_spec)
    elif isinstance(input_spec, IntegerInputV3):
        schema = _compile_integer_input(input_spec)
    elif isinstance(input_spec, BooleanInputV3):
        schema = {"type": "boolean"}
    elif isinstance(input_spec, EnumInputV3):
        schema = {"enum": deepcopy(input_spec.values)}
    elif isinstance(input_spec, JsonSchemaInputV3):
        schema = deepcopy(input_spec.schema)
        defs = _hoist_json_schema_defs(schema, path)
    else:
        msg = f"{path}.kind is not a plugin-owned input kind"
        raise TypeError(msg)

    compiled = _apply_common_metadata(schema, input_spec)
    _validate_common_values(compiled, defs, input_spec, path)
    return compiled, defs


def _rewrite_local_definition_refs(value: Any, names: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _rewrite_local_definition_refs(item, names)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_local_definition_refs(item, names) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        name = value.removeprefix("#/$defs/")
        renamed = names.get(name)
        if renamed is not None:
            return f"#/$defs/{renamed}"
    return value


def _hoist_json_schema_defs(
    schema: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    raw_defs = schema.pop("$defs", None)
    if raw_defs is None:
        return {}
    if not isinstance(raw_defs, dict):
        msg = f"{path}.schema.$defs must be an object"
        raise TypeError(msg)

    prefix = re.sub(r"[^A-Za-z0-9_]+", "__", path).strip("_")
    names = {name: f"{prefix}__{name}" for name in raw_defs}
    rewritten_schema = _rewrite_local_definition_refs(schema, names)
    schema.clear()
    schema.update(rewritten_schema)
    return {
        names[name]: _rewrite_local_definition_refs(definition, names)
        for name, definition in raw_defs.items()
    }


def _compile_spatial_input(
    input_spec: LocationInputV3 | BoundsInputV3,
) -> tuple[dict[str, Any], dict[str, Any], SpatialInputKindV3]:
    kind: SpatialInputKindV3 = input_spec.kind
    schema, defs = _wrapper_field_schema(kind)
    compiled = deepcopy(schema)
    if kind == "location":
        compiled["description"] = _LOCATION_DESCRIPTION
        compiled["examples"] = deepcopy(_LOCATION_EXAMPLES)
    else:
        compiled["description"] = _BOUNDS_DESCRIPTION
        compiled["examples"] = deepcopy(_BOUNDS_EXAMPLES)
    return compiled, defs, kind


def _compile_batch_input(
    input_spec: BatchInputV3,
    path: str,
    field_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value_schema, defs = _compile_plugin_owned_input(input_spec.value, f"{path}.value")
    properties = {
        "key": deepcopy(_BATCH_KEY_SCHEMA),
        "value": value_schema,
    }
    if input_spec.label:
        properties["label"] = deepcopy(_BATCH_LABEL_SCHEMA)

    schema = {
        "type": "array",
        "minItems": 1,
        "maxItems": input_spec.max_items,
        "uniqueItems": True,
        "description": (
            f"Keyed batch values for {field_name!r}. Each item contains a stable "
            "key, a plugin-defined value, and optionally a display label."
        ),
        "items": {
            "type": "object",
            "required": ["key", "value"],
            "additionalProperties": False,
            "properties": properties,
        },
    }
    return schema, defs


def _compile_input_property(
    input_spec: InputSpecV3,
    path: str,
    field_name: str,
) -> tuple[dict[str, Any], dict[str, Any], SpatialInputKindV3 | None]:
    if isinstance(input_spec, LocationInputV3 | BoundsInputV3):
        return _compile_spatial_input(input_spec)
    if isinstance(input_spec, BatchInputV3):
        schema, defs = _compile_batch_input(input_spec, path, field_name)
        return schema, defs, None

    schema, defs = _compile_plugin_owned_input(input_spec, path)
    return schema, defs, None


def _merge_defs(
    root_defs: dict[str, Any],
    defs: dict[str, Any],
    path: str,
) -> None:
    for name, definition in defs.items():
        existing_definition = root_defs.get(name)
        if existing_definition is not None and existing_definition != definition:
            msg = f"{path} conflicts with canonical schema definition {name!r}"
            raise ValueError(msg)
        root_defs[name] = definition


def _compile_metric_request_schema(
    metric: MetricManifestV3,
    metric_index: int,
) -> tuple[dict[str, Any], dict[str, SpatialInputKindV3], list[str]]:
    required: list[str] = []
    properties: dict[str, Any] = {}
    spatial_inputs: dict[str, SpatialInputKindV3] = {}
    batch_inputs: list[str] = []
    root_defs: dict[str, Any] = {}

    for field_name, input_spec in metric.inputs.items():
        path = f"metrics[{metric_index}].inputs.{field_name}"
        property_schema, defs, spatial_kind = _compile_input_property(
            input_spec,
            path,
            field_name,
        )
        properties[field_name] = property_schema
        if (
            not isinstance(input_spec, PluginOwnedInputMetadataV3)
            or input_spec.required
        ):
            required.append(field_name)
        if spatial_kind is not None:
            spatial_inputs[field_name] = spatial_kind
        if isinstance(input_spec, BatchInputV3):
            batch_inputs.append(field_name)
        _merge_defs(root_defs, defs, path)

    request_schema: dict[str, Any] = {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }
    if root_defs:
        request_schema["$defs"] = root_defs

    _validate_json_schema(request_schema, f"metrics[{metric_index}].request_schema")
    return request_schema, spatial_inputs, batch_inputs


def compile_plugin_manifest(manifest: PluginManifestV3) -> CompiledPluginManifestV3:
    """Compile a schema v3 authoring manifest into Lyra's runtime contract."""

    compiled_metrics: list[CompiledMetricManifestV3] = []
    for index, metric in enumerate(manifest.metrics):
        request_schema, spatial_inputs, batch_inputs = _compile_metric_request_schema(
            metric,
            index,
        )
        compiled_metrics.append(
            CompiledMetricManifestV3(
                name=metric.name,
                description=metric.description,
                entrypoint=metric.entrypoint,
                spatial_inputs=spatial_inputs,
                batch_inputs=batch_inputs,
                request_schema=request_schema,
                output=metric.output.model_copy(deep=True),
            )
        )

    return CompiledPluginManifestV3(
        schema_version=3,
        plugin=manifest.plugin.model_copy(deep=True),
        metrics=compiled_metrics,
    )


__all__ = [
    "BatchInputV3",
    "BatchedTableOutputColumnV3",
    "BooleanInputV3",
    "BoundsInputV3",
    "CompiledMetricManifestV3",
    "CompiledPluginManifestV3",
    "EnumInputV3",
    "FileOutputV3",
    "FractionOfLocationAreaDerivationV3",
    "InputSpecV3",
    "IntegerInputV3",
    "JsonSchemaInputV3",
    "LocationInputV3",
    "MetricManifestV3",
    "NumberInputV3",
    "OutputColumnTypeV3",
    "OutputSpecV3",
    "PluginInfoV3",
    "PluginManifestV3",
    "PluginOwnedInputSpecV3",
    "SpatialInputKindV3",
    "StringInputV3",
    "TableOutputColumnV3",
    "TableOutputV3",
    "compile_plugin_manifest",
    "expand_runner_table_output_columns",
    "expand_table_output_columns",
]
