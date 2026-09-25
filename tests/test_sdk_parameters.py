from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

import pytest
from lyra.sdk import (
    LocationInput,
    MetricInputError,
    MetricParameters,
    PluginDefinition,
    PluginDefinitionError,
    metric,
)
from lyra.sdk.parameters import validate_parameter_model
from lyra.sdk.schema import check_request_schema
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    RootModel,
    create_model,
    field_serializer,
    model_validator,
)

from tests.fixtures.contract_plugin.metrics import CAPACITY_OUTPUT

if TYPE_CHECKING:
    from collections.abc import Iterator


def register(model: type[BaseModel]) -> PluginDefinition:
    def handler(parameters: BaseModel, location: LocationInput) -> object:
        return parameters, location

    handler.__annotations__["parameters"] = model
    decorated = metric(name="test", description="Test", output=CAPACITY_OUTPUT)(handler)
    assert decorated is handler
    return PluginDefinition(metrics=[handler])


@pytest.mark.parametrize(
    "annotation",
    [
        Any,
        tuple[int, ...],
        set[str],
        bytes,
        date,
        Decimal,
        dict[int, str],
        list,
        dict,
        Literal[1, "a"],
        Annotated[int, PlainSerializer(str)],
    ],
)
def test_unsupported_types(annotation: object) -> None:
    model = create_model(
        "Parameters",
        __base__=MetricParameters,
        value=(annotation, Field(description="Value")),
    )
    with pytest.raises(PluginDefinitionError, match=r"parameters\.value"):
        register(model)


@pytest.mark.parametrize(
    "field",
    [
        Field(description="Value", alias="other"),
        Field(description="Value", default_factory=list),
        Field(default=-1, ge=0, description="Value"),
        Field(description="Value", examples=["wrong"]),
        Field(description="Value", json_schema_extra={"type": "string"}),
        Field(default=1, description="Value", validate_default=False),
        Field(),
    ],
)
def test_invalid_field_contracts(field: object) -> None:
    model = create_model("Parameters", __base__=MetricParameters, value=(int, field))
    with pytest.raises(PluginDefinitionError):
        register(model)


class Permissive(BaseModel):
    value: int = Field(description="Value")


class Recursive(MetricParameters):
    children: list[Recursive] = Field(description="Children")


class Serializer(MetricParameters):
    value: int = Field(description="Value")

    @field_serializer("value")
    @staticmethod
    def serialize(value: int) -> str:
        return str(value)


@pytest.mark.parametrize("model", [Permissive, Recursive, Serializer, RootModel[int]])
def test_unsupported_models(model: type[BaseModel]) -> None:
    with pytest.raises(PluginDefinitionError):
        validate_parameter_model(model, "test")


def test_nested_model_configuration() -> None:
    model = create_model(
        "Parameters",
        __base__=MetricParameters,
        nested=(Permissive, Field(description="Nested")),
    )
    with pytest.raises(PluginDefinitionError, match=r"parameters\.nested"):
        register(model)


class Values(MetricParameters):
    choice: Literal["only"] = Field(description="Choice")
    values: list[int | None] = Field(description="Values")
    lookup: dict[str, float] = Field(description="Lookup")


def test_json_types_and_local_definitions() -> None:
    plugin = register(Values)
    good = {"choice": "only", "values": [1, None], "lookup": {"a": 1.5}}
    assert plugin.prepare_parameters("test", good).model_dump() == good
    schema = plugin.describe("test").request_schema
    check_request_schema(schema)
    assert schema["$defs"]["Values"]["properties"]["choice"]["const"] == "only"
    for value in [{**good, "lookup": {1: 2}}, {**good, "lookup": {"a": float("nan")}}]:
        with pytest.raises(MetricInputError):
            plugin.prepare_parameters("test", value)


class Semantic(MetricParameters):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"lower": 2, "upper": 1}]}
    )
    lower: int = Field(description="Lower", examples=[1])
    upper: int = Field(description="Upper", examples=[2])

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower > self.upper:
            message = "invalid interval"
            raise ValueError(message)
        return self


def test_examples_do_not_run_semantic_validation() -> None:
    plugin = register(Semantic)
    with pytest.raises(MetricInputError, match="invalid interval"):
        plugin.prepare_parameters("test", {"lower": 2, "upper": 1})


def test_rejects_handler_signatures() -> None:
    def missing(location: LocationInput) -> object:
        return location

    def positional(location: LocationInput, /) -> object:
        return location

    def variadic(*location: LocationInput) -> object:
        return location

    def keywords(**location: LocationInput) -> object:
        return location

    def default(location: LocationInput | None = None) -> object:
        return location

    def unknown(other: LocationInput) -> object:
        return other

    def no_spatial(parameters: MetricParameters) -> object:
        return parameters

    missing.__annotations__.pop("location")
    for handler in [
        missing,
        positional,
        variadic,
        keywords,
        default,
        unknown,
        no_spatial,
    ]:
        with pytest.raises(PluginDefinitionError):
            metric(name="test", description="Test", output=CAPACITY_OUTPUT)(handler)


def test_return_annotation_is_only_descriptive() -> None:
    def handler(*, location: LocationInput) -> object:
        return location

    handler.__annotations__["return"] = "UnimportedDataFrame"
    metric(name="test", description="Test", output=CAPACITY_OUTPUT)(handler)
    assert PluginDefinition(metrics=[handler]).metric_names == ("test",)


def test_registration_rejects_duplicates_and_undecorated() -> None:
    def handler(location: LocationInput) -> object:
        return location

    with pytest.raises(PluginDefinitionError):
        PluginDefinition(metrics=[handler])
    decorated = metric(name="test", description="Test", output=CAPACITY_OUTPUT)(handler)
    with pytest.raises(PluginDefinitionError, match="duplicate"):
        PluginDefinition(metrics=[decorated, decorated])


@pytest.mark.parametrize(
    "reference", ["https://example.com/schema", "#/$defs/missing", "#/properties/value"]
)
def test_disallow_external_or_invalid_schema_refs(reference: str) -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": reference,
    }
    with pytest.raises(ValueError, match="reference"):
        check_request_schema(schema)


def test_defaults_containing_ref_are_data() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "default": {"$ref": "ordinary data"},
    }
    check_request_schema(schema)


def test_pydantic_schema_generation_failure_has_metric_context() -> None:
    class Parameters(MetricParameters):
        mapping: dict[str, str] = Field(
            default={"$ref": "ordinary data"}, description="Mapping"
        )

    with pytest.raises(PluginDefinitionError, match=r"test.*request schema"):
        register(Parameters)


def test_rejects_async_and_generator() -> None:
    async def asynchronous(location: LocationInput) -> object:
        await asyncio.sleep(0)
        return location

    def generator(location: LocationInput) -> Iterator[LocationInput]:
        yield location

    for handler in [asynchronous, generator]:
        with pytest.raises(PluginDefinitionError, match="synchronous"):
            metric(name="test", description="Test", output=CAPACITY_OUTPUT)(handler)


def test_strict_models_can_reject_schema_valid_numbers() -> None:
    class Strict(MetricParameters):
        value: int = Field(strict=True, description="Integer")

    plugin = register(Strict)
    assert plugin.prepare_parameters("test", {"value": 1}).model_dump() == {"value": 1}
    with pytest.raises(MetricInputError):
        plugin.prepare_parameters("test", {"value": 1.0})
