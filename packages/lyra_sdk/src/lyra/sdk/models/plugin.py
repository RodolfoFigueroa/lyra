"""Format-5 plugin manifests and static table/file output declarations."""

import re
from typing import Annotated, Literal, Self

from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.schema import check_request_schema
from lyra.sdk.types import JsonObject
from lyra.sdk.units import Unit
from pydantic import Field, field_validator, model_validator

SpatialInputKind = Literal["location", "bounds"]
OutputColumnType = Literal["integer", "number", "string", "boolean"]
_PUBLIC_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_FACTORY_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


def validate_metric_name(name: str) -> str:
    """Validate a public metric identifier.

    Returns:
        The identifier unchanged.

    Raises:
        ValueError: If the name is invalid or reserved.
    """
    if not _PUBLIC_NAME_PATTERN.fullmatch(name) or name.startswith("lyra_"):
        msg = "Metric name must match ^[a-z][a-z0-9_]*$ and not start with 'lyra_'."
        raise ValueError(msg)
    return name


class PluginInfo(StrictBaseModel):
    """Identity captured from a plugin project."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class FractionOfLocationArea(StrictBaseModel):
    """A server-derived fraction of the corresponding location's area."""

    kind: Literal["fraction_of_location_area"] = "fraction_of_location_area"
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class TableColumn(StrictBaseModel):
    """One declared scalar output column, optionally followed by a derivation."""

    name: str = Field(min_length=1)
    type: OutputColumnType
    unit: Unit | None = Field(
        description=(
            "Canonical output unit; explicit null means not applicable, not unknown."
        )
    )
    description: str = Field(min_length=1)
    nullable: bool = False
    derivations: list[FractionOfLocationArea] = Field(
        default_factory=list,
        max_length=1,
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def validate_derivations(self) -> Self:
        """Require a numeric square-metre source for area fractions.

        Returns:
            This validated column.

        Raises:
            ValueError: If the source has the wrong unit or type.
        """
        if self.derivations and (
            self.type not in {"integer", "number"} or self.unit != Unit.SQUARE_METRE
        ):
            msg = "Area fractions require a numeric source column with unit 'm2'."
            raise ValueError(msg)
        return self


class TableOutput(StrictBaseModel):
    """An ordered, fixed set of scalar columns aligned to location features."""

    kind: Literal["table"] = "table"
    columns: list[TableColumn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_columns(self) -> Self:
        """Require distinct names across source and derived columns.

        Returns:
            This validated table declaration.

        Raises:
            ValueError: If any effective column name is duplicated.
        """
        names = [
            name
            for column in self.columns
            for name in [column.name, *(item.name for item in column.derivations)]
        ]
        if len(names) != len(set(names)):
            msg = "Source and derived column names must be unique."
            raise ValueError(msg)
        return self


def effective_table_columns(output: TableOutput) -> list[TableColumn]:
    """Return source and derived column contracts in terminal result order."""
    columns: list[TableColumn] = []
    for source in output.columns:
        columns.append(source.model_copy(deep=True))
        columns.extend(
            TableColumn(
                name=item.name,
                type="number",
                unit=Unit.RATIO,
                description=item.description,
                nullable=source.nullable,
            )
            for item in source.derivations
        )
    return columns


class FileOutput(StrictBaseModel):
    """A file artifact created inside the job temporary directory."""

    kind: Literal["file"] = "file"
    media_type: str = Field(min_length=1)
    extensions: list[str] = Field(min_length=1)

    @field_validator("extensions")
    @classmethod
    def validate_extensions(cls, extensions: list[str]) -> list[str]:
        """Require nonempty, case-insensitively unique dot-prefixed suffixes.

        Returns:
            The extensions in declared order.

        Raises:
            ValueError: If a suffix is malformed or repeated.
        """
        if any(not item.startswith(".") or len(item) == 1 for item in extensions):
            msg = "File extensions must start with '.' and include a suffix."
            raise ValueError(msg)
        if len({item.lower() for item in extensions}) != len(extensions):
            msg = "File extensions must be unique ignoring case."
            raise ValueError(msg)
        return extensions


OutputSpec = Annotated[TableOutput | FileOutput, Field(discriminator="kind")]


class MetricManifest(StrictBaseModel):
    """One discoverable metric with its complete unresolved request schema."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    request_schema: JsonObject
    spatial_inputs: dict[str, SpatialInputKind] = Field(min_length=1)
    output: OutputSpec

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        """Return a valid, nonreserved metric identifier."""
        return validate_metric_name(name)

    @field_validator("description")
    @classmethod
    def validate_description(cls, description: str) -> str:
        """Return a nonblank description.

        Raises:
            ValueError: If the description contains only whitespace.
        """
        if not description.strip():
            msg = "Metric description must not be blank."
            raise ValueError(msg)
        return description

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Check spatial metadata and the self-contained root request contract.

        Returns:
            This validated metric.

        Raises:
            ValueError: If root fields, spatial conventions, or schema are invalid.
        """
        check_request_schema(self.request_schema)
        if any(name != kind for name, kind in self.spatial_inputs.items()):
            msg = "Spatial fields must use their canonical location/bounds names."
            raise ValueError(msg)
        if (
            isinstance(self.output, TableOutput)
            and "location" not in self.spatial_inputs
        ):
            msg = "Table metrics require location."
            raise ValueError(msg)
        properties = self.request_schema.get("properties")
        expected = set(self.spatial_inputs)
        if isinstance(properties, dict) and "parameters" in properties:
            expected.add("parameters")
        required = self.request_schema.get("required")
        valid_fields = isinstance(properties, dict) and set(properties) == expected
        valid_required = (
            isinstance(required, list)
            and required == list(dict.fromkeys(required))
            and set(required) == expected
        )
        if (
            self.request_schema.get("type") != "object"
            or self.request_schema.get("additionalProperties") is not False
            or not valid_fields
            or not valid_required
        ):
            msg = (
                "Request schema must require exactly the declared "
                "spatial/parameter fields."
            )
            raise ValueError(msg)
        return self


class PluginManifest(StrictBaseModel):
    """The sole persisted plugin contract; earlier formats are unsupported."""

    schema_version: Literal[5] = 5
    plugin: PluginInfo
    factory: str
    metrics: list[MetricManifest] = Field(min_length=1)

    @field_validator("factory")
    @classmethod
    def validate_factory(cls, factory: str) -> str:
        """Return a valid module:attribute factory reference.

        Raises:
            ValueError: If the reference has unsupported syntax.
        """
        if not _FACTORY_PATTERN.fullmatch(factory):
            msg = "Factory must use module:attribute syntax."
            raise ValueError(msg)
        return factory

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        """Require unique metric names and store them in deterministic order.

        Returns:
            This manifest with sorted metrics.

        Raises:
            ValueError: If metric names repeat.
        """
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            msg = "Plugin metric names must be unique."
            raise ValueError(msg)
        self.metrics = sorted(self.metrics, key=lambda metric: metric.name)
        return self
