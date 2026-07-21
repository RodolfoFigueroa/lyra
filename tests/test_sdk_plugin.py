from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import pytest
from jsonschema.validators import validator_for
from lyra.sdk import (
    BatchInput,
    BatchItem,
    BoundsInput,
    Input,
    LocationInput,
    LyraDB,
    PluginDefinition,
    PluginDefinitionError,
    RunContext,
    metric,
)
from lyra.sdk.models import JobEnvelope, JobMessageLevel, TableJobResult
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.plugin_v4 import (
    BatchedTableOutputColumnV4,
    PluginInfoV4,
    TableOutputColumnV4,
    TableOutputV4,
)
from pydantic import AfterValidator, BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.sdk.types import JsonValue


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _feature_collection() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "id": "area-1",
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-99.2, 19.3],
                            [-99.1, 19.3],
                            [-99.1, 19.4],
                            [-99.2, 19.4],
                            [-99.2, 19.3],
                        ]
                    ],
                },
                "properties": {},
            }
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


def _table_output() -> TableOutputV4:
    return TableOutputV4(
        kind="table",
        columns=[
            TableOutputColumnV4(
                name="value",
                type="integer",
                unit="count",
                description="Example value.",
            )
        ],
    )


_FAKE_DB = cast("LyraDB", object())


@dataclass
class FakeContext:
    job_id: str = "job-1"
    metric: str = "example"
    logger: Any = None
    temp_dir: Path = Path()
    db: LyraDB = _FAKE_DB

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        del stage, current, total, unit, message

    def report_message(
        self,
        message: str,
        *,
        level: JobMessageLevel = "info",
        fields: dict[str, Any] | None = None,
    ) -> None:
        del message, level, fields

    def check_cancelled(self) -> None:
        return None


class YearRange(BaseModel):
    start: int
    end: int


class Options(BaseModel):
    years: list[int]
    year_range: YearRange


def _require_even(value: int) -> int:
    if value % 2:
        msg = "value must be even"
        raise ValueError(msg)
    return value


def test_run_context_database_is_non_nullable() -> None:
    getter = RunContext.db.fget
    assert getter is not None
    assert getter.__annotations__["return"] == "LyraDB"


def test_typed_metric_generates_manifest_and_receives_parsed_values() -> None:
    received: dict[str, Any] = {}

    @metric(
        name="example",
        description="Example metric.",
        inputs={
            "value": Input(
                description="Submitted value.",
                examples=[3],
                ge=1,
                le=10,
            ),
            "mode": Input(description="Calculation mode."),
            "threshold": Input(description="Optional score threshold."),
        },
        output=_table_output(),
    )
    def calculate(
        location: LocationInput,
        value: int = 3,
        mode: Literal["fast", "accurate"] = "fast",
        threshold: float | None = None,
        *,
        context: RunContext,
    ) -> TableJobResult:
        received.update(
            location=location,
            value=value,
            mode=mode,
            threshold=threshold,
            context=context,
        )
        return TableJobResult(
            job_id=context.job_id,
            index=[location.features[0].id],
            columns=["value"],
            data=[[value]],
        )

    plugin = PluginDefinition(metrics=[calculate])
    assert calculate.__name__ == "calculate"
    manifest = plugin.manifest(
        plugin=PluginInfoV4(name="example-plugin", version="1.0.0"),
        factory="example.metrics:create_plugin",
    )
    metric_contract = manifest.metrics[0]
    assert metric_contract.inputs["location"].kind == "location"
    assert metric_contract.inputs["value"].model_dump(exclude_none=True) == {
        "kind": "integer",
        "description": "Submitted value.",
        "default": 3,
        "examples": [3],
        "required": False,
        "nullable": False,
        "minimum": 1,
        "maximum": 10,
    }
    assert metric_contract.inputs["mode"].kind == "enum"
    assert metric_contract.inputs["threshold"].model_dump(exclude_none=True) == {
        "kind": "number",
        "description": "Optional score threshold.",
        "required": False,
        "nullable": True,
    }
    assert "default" in metric_contract.inputs["threshold"].model_fields_set
    assert getattr(metric_contract.inputs["threshold"], "default", "missing") is None

    context = FakeContext()
    result = plugin(
        JobEnvelope(
            job_id="job-1",
            metric="example",
            input={"location": _feature_collection()},
        ),
        context,
    )

    assert isinstance(received["location"], GeoJSON)
    assert received["value"] == 3
    assert received["mode"] == "fast"
    assert received["threshold"] is None
    assert received["context"] is context
    assert isinstance(result, TableJobResult)
    assert result.data == [[3]]

    description = plugin.describe("example")
    assert description.name == "example"
    assert description.inputs == metric_contract.inputs
    assert "Annotated" not in description.signature
    assert "value: int = 3" in description.signature

    with pytest.raises(PluginDefinitionError, match="available metrics: example"):
        plugin.describe("missing")


def test_bounds_and_nested_model_compile_and_parse() -> None:
    received: dict[str, Any] = {}

    @metric(
        name="file_like",
        description="Bounds metric.",
        inputs={"options": Input(description="Calculation options.")},
        output=_table_output(),
    )
    def calculate(
        location: LocationInput,
        bounds: BoundsInput,
        options: Options,
        *,
        context: RunContext,
    ) -> TableJobResult:
        del location
        received.update(bounds=bounds, options=options)
        return TableJobResult(
            job_id=context.job_id,
            index=[bounds.features[0].id],
            columns=["value"],
            data=[[len(options.years)]],
        )

    plugin = PluginDefinition(metrics=[calculate])
    compiled = plugin.compiled_manifest(
        plugin=PluginInfoV4(name="example-plugin", version="1.0.0"),
        factory="example.metrics:create_plugin",
    )
    schema = compiled.metrics[0].request_schema
    assert any(name.endswith("__YearRange") for name in _json_object(schema["$defs"]))
    validator_for(schema).check_schema(schema)

    context = FakeContext(metric="file_like")
    result = plugin(
        JobEnvelope(
            job_id="job-1",
            metric="file_like",
            input={
                "bounds": _feature_collection(),
                "location": _feature_collection(),
                "options": {
                    "years": [2024, 2025],
                    "year_range": {"start": 2024, "end": 2025},
                },
            },
        ),
        context,
    )

    assert isinstance(received["bounds"], SingleGeoJSON)
    assert isinstance(received["options"], Options)
    assert isinstance(result, TableJobResult)
    assert result.data == [[2]]


def test_batch_inputs_generate_contract_and_parse_items() -> None:
    received: list[BatchItem[str]] = []
    output = TableOutputV4(
        kind="table",
        batched_columns=[
            BatchedTableOutputColumnV4(
                source="categories",
                name="value_{key}",
                type="integer",
                unit="count",
                description="Value for {label}.",
            )
        ],
    )

    @metric(
        name="batch_metric",
        description="Batch metric.",
        inputs={
            "categories": BatchInput(
                max_items=3,
                allow_labels=True,
                items=Input(
                    description="Category identifier.",
                    examples=["park"],
                    min_length=2,
                ),
            )
        },
        output=output,
    )
    def calculate(
        location: LocationInput,
        categories: list[BatchItem[str]],
        *,
        context: RunContext,
    ) -> TableJobResult:
        del location
        received.extend(categories)
        return TableJobResult(
            job_id=context.job_id,
            index=["area-1"],
            columns=[f"value_{item.key}" for item in categories],
            data=[[1 for _item in categories]],
        )

    plugin = PluginDefinition(metrics=[calculate])
    manifest = plugin.manifest(
        plugin=PluginInfoV4(name="batch-plugin", version="1.0.0"),
        factory="batch.metrics:create_plugin",
    )
    batch = manifest.metrics[0].inputs["categories"]
    assert batch.kind == "batch"
    assert batch.max_items == 3
    assert batch.label is True
    assert batch.value.description == "Category identifier."
    assert batch.value.examples == ["park"]

    compiled = plugin.compiled_manifest(
        plugin=PluginInfoV4(name="batch-plugin", version="1.0.0"),
        factory="batch.metrics:create_plugin",
    )
    properties = _json_object(compiled.metrics[0].request_schema["properties"])
    batch_schema = _json_object(properties["categories"])
    description = batch_schema["description"]
    assert isinstance(description, str)
    assert description.startswith("Keyed batch values")
    items = _json_object(batch_schema["items"])
    item_properties = _json_object(items["properties"])
    value_schema = _json_object(item_properties["value"])
    assert value_schema["description"] == "Category identifier."
    assert value_schema["examples"] == ["park"]

    plugin(
        JobEnvelope(
            job_id="job-1",
            metric="batch_metric",
            input={
                "location": _feature_collection(),
                "categories": [
                    {"key": "parks", "value": "park", "label": "Parks"},
                    {"key": "food", "value": "restaurant"},
                ],
            },
        ),
        FakeContext(metric="batch_metric"),
    )

    assert [item.key for item in received] == ["parks", "food"]
    assert all(isinstance(item, BatchItem) for item in received)

    for categories, match in (
        (
            [
                {"key": "same", "value": "park"},
                {"key": "same", "value": "food"},
            ],
            "duplicate key",
        ),
        (
            [
                {"key": "one", "value": "park"},
                {"key": "two", "value": "food"},
                {"key": "three", "value": "shops"},
                {"key": "four", "value": "schools"},
            ],
            "between 1 and 3",
        ),
    ):
        with pytest.raises(PluginDefinitionError, match=match):
            plugin(
                JobEnvelope(
                    job_id="job-1",
                    metric="batch_metric",
                    input={
                        "location": _feature_collection(),
                        "categories": categories,
                    },
                ),
                FakeContext(metric="batch_metric"),
            )


def test_batch_input_rejects_labels_when_disabled() -> None:
    output = TableOutputV4(
        kind="table",
        batched_columns=[
            BatchedTableOutputColumnV4(
                source="categories",
                name="value_{key}",
                type="integer",
                unit="count",
                description="Value for {key}.",
            )
        ],
    )

    @metric(
        name="unlabelled_batch",
        description="Unlabelled batch metric.",
        inputs={
            "categories": BatchInput(
                max_items=3,
                items=Input(description="Category identifier."),
            )
        },
        output=output,
    )
    def calculate(
        location: LocationInput,
        categories: list[BatchItem[str]],
    ) -> TableJobResult:
        raise AssertionError(location, categories)

    plugin = PluginDefinition(metrics=[calculate])
    with pytest.raises(PluginDefinitionError, match="does not accept labels"):
        plugin(
            JobEnvelope(
                job_id="job-1",
                metric="unlabelled_batch",
                input={
                    "location": _feature_collection(),
                    "categories": [{"key": "parks", "value": "park", "label": "Parks"}],
                },
            ),
            FakeContext(metric="unlabelled_batch"),
        )


def test_protocol_owned_input_metadata_is_rejected() -> None:
    with pytest.raises(PluginDefinitionError, match="Lyra-owned input"):

        @metric(
            name="spatial_metadata",
            description="Invalid spatial metadata.",
            inputs={"location": Input(description="Plugin-owned location.")},
            output=_table_output(),
        )
        def spatial_metadata(location: LocationInput) -> TableJobResult:
            raise AssertionError(location)

    with pytest.raises(PluginDefinitionError, match="Field metadata"):

        @metric(
            name="batch_metadata",
            description="Invalid batch metadata.",
            inputs={
                "categories": BatchInput(
                    max_items=3,
                    items=Input(description="Category identifier."),
                )
            },
            output=TableOutputV4(
                kind="table",
                batched_columns=[
                    BatchedTableOutputColumnV4(
                        source="categories",
                        name="value_{key}",
                        type="integer",
                        unit="count",
                        description="Value for {label}.",
                    )
                ],
            ),
        )
        def batch_metadata(
            location: LocationInput,
            categories: list[
                BatchItem[
                    Annotated[
                        str,
                        Field(description="Ambiguous category description."),
                    ]
                ]
            ],
        ) -> TableJobResult:
            raise AssertionError(location, categories)


def test_input_declaration_names_are_checked_together() -> None:
    with pytest.raises(PluginDefinitionError) as error:

        @metric(
            name="invalid_declarations",
            description="Invalid declarations.",
            inputs={
                "location": Input(description="Invalid spatial metadata."),
                "unknown": Input(description="Unknown input."),
            },
            output=_table_output(),
        )
        def invalid_declarations(
            location: LocationInput,
            value: int,
        ) -> TableJobResult:
            raise AssertionError(location, value)

    message = str(error.value)
    assert "unknown declaration(s): unknown" in message
    assert "Lyra-owned input(s) must not be declared: location" in message
    assert "missing declaration(s): value" in message
    assert "Handler: invalid_declarations(" in message


def test_input_declaration_kind_must_match_batch_annotation() -> None:
    with pytest.raises(PluginDefinitionError, match="must use BatchInput"):

        @metric(
            name="wrong_batch_declaration",
            description="Wrong batch declaration.",
            inputs={"categories": Input(description="Category identifier.")},
            output=_table_output(),
        )
        def wrong_batch_declaration(
            location: LocationInput,
            categories: list[BatchItem[str]],
        ) -> TableJobResult:
            raise AssertionError(location, categories)


def test_input_supports_custom_validators_and_json_schema_metadata() -> None:
    @metric(
        name="custom_input",
        description="Custom input metadata.",
        inputs={
            "value": Input(
                description="Even value.",
                json_schema_extra={"x-lyra-ui": {"widget": "slider"}},
            )
        },
        output=_table_output(),
    )
    def calculate(
        location: LocationInput,
        value: Annotated[int, AfterValidator(_require_even)],
    ) -> TableJobResult:
        return TableJobResult(
            job_id="job-1",
            index=[location.features[0].id],
            columns=["value"],
            data=[[value]],
        )

    plugin = PluginDefinition(metrics=[calculate])
    compiled = plugin.compiled_manifest(
        plugin=PluginInfoV4(name="custom-plugin", version="1.0.0"),
        factory="custom.metrics:create_plugin",
    )
    properties = _json_object(compiled.metrics[0].request_schema["properties"])
    value_schema = _json_object(properties["value"])
    assert value_schema["x-lyra-ui"] == {"widget": "slider"}

    with pytest.raises(PluginDefinitionError, match="value must be even"):
        plugin(
            JobEnvelope(
                job_id="job-1",
                metric="custom_input",
                input={"location": _feature_collection(), "value": 3},
            ),
            FakeContext(metric="custom_input"),
        )


def test_input_authoring_models_validate_configuration() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        Input(description="  ")
    with pytest.raises(ValueError, match="at least one example"):
        Input(description="Value.", examples=[])
    with pytest.raises(ValueError, match="at least 1"):
        BatchInput(
            max_items=0,
            items=Input(description="Batch value."),
        )


@pytest.mark.parametrize(
    ("function", "match"),
    [
        (lambda value: value, "must have an annotation"),
        (lambda *values: values, "positional-or-keyword or keyword-only"),
    ],
)
def test_invalid_metric_signatures_fail_at_registration(
    function: Callable[..., JsonValue | tuple[JsonValue, ...]],
    match: str,
) -> None:
    with pytest.raises(PluginDefinitionError, match=match):
        metric(
            name="invalid",
            description="Invalid metric.",
            output=_table_output(),
        )(function)


def test_runtime_adapter_rejects_unknown_fields_and_duplicate_batch_keys() -> None:
    @metric(
        name="example",
        description="Example metric.",
        output=_table_output(),
    )
    def calculate(location: LocationInput) -> TableJobResult:
        del location
        raise AssertionError

    plugin = PluginDefinition(metrics=[calculate])
    with pytest.raises(PluginDefinitionError, match="unexpected input"):
        plugin(
            JobEnvelope(
                job_id="job-1",
                metric="example",
                input={"location": _feature_collection(), "extra": 1},
            ),
            FakeContext(),
        )


def test_plugin_definition_requires_explicit_decorated_metrics() -> None:
    def undecorated(location: LocationInput) -> TableJobResult:
        raise AssertionError(location)

    with pytest.raises(PluginDefinitionError, match="at least one decorated metric"):
        PluginDefinition(metrics=[])
    with pytest.raises(PluginDefinitionError, match="is not decorated"):
        PluginDefinition(metrics=[undecorated])


def test_plugin_definition_rejects_duplicate_names() -> None:
    @metric(name="duplicate", description="First.", output=_table_output())
    def first(location: LocationInput) -> TableJobResult:
        raise AssertionError(location)

    @metric(name="duplicate", description="Second.", output=_table_output())
    def second(location: LocationInput) -> TableJobResult:
        raise AssertionError(location)

    with pytest.raises(PluginDefinitionError, match="Duplicate metric name"):
        PluginDefinition(metrics=[first, second])


def test_metric_handler_cannot_be_decorated_twice() -> None:
    def calculate(location: LocationInput) -> TableJobResult:
        raise AssertionError(location)

    decorated = metric(
        name="once",
        description="Decorated once.",
        output=_table_output(),
    )(calculate)
    assert decorated is calculate

    with pytest.raises(PluginDefinitionError, match="already decorated"):
        metric(
            name="twice",
            description="Decorated twice.",
            output=_table_output(),
        )(calculate)
