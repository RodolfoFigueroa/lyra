from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from jsonschema.validators import validator_for
from lyra.sdk import (
    Batch,
    BatchItem,
    BoundsInput,
    LocationInput,
    PluginDefinition,
    PluginDefinitionError,
    RunContext,
)
from lyra.sdk.models import JobEnvelope, TableJobResult
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.plugin_v3 import (
    BatchedTableOutputColumnV3,
    PluginInfoV3,
    TableOutputColumnV3,
    TableOutputV3,
)
from pydantic import BaseModel, Field


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


def _table_output() -> TableOutputV3:
    return TableOutputV3(
        kind="table",
        columns=[
            TableOutputColumnV3(
                name="value",
                type="integer",
                unit="count",
                description="Example value.",
            )
        ],
    )


@dataclass
class FakeContext:
    job_id: str = "job-1"
    metric: str = "example"
    logger: Any = None
    temp_dir: Path = Path()
    db: Any = None

    def emit_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        del event, data

    def check_cancelled(self) -> None:
        return None


class YearRange(BaseModel):
    start: int
    end: int


class Options(BaseModel):
    years: list[int]
    year_range: YearRange


def test_typed_metric_generates_manifest_and_receives_parsed_values() -> None:
    plugin = PluginDefinition()
    received: dict[str, Any] = {}

    @plugin.metric(
        name="example",
        description="Example metric.",
        output=_table_output(),
    )
    def calculate(
        location: LocationInput,
        value: Annotated[
            int,
            Field(description="Submitted value.", ge=1, le=10, examples=[3]),
        ] = 3,
        mode: Literal["fast", "accurate"] = "fast",
        *,
        context: RunContext,
    ) -> TableJobResult:
        received.update(location=location, value=value, mode=mode, context=context)
        return TableJobResult(
            job_id=context.job_id,
            index=[location.features[0].id],
            columns=["value"],
            data=[[value]],
        )

    assert calculate.__name__ == "calculate"
    manifest = plugin.manifest(
        plugin=PluginInfoV3(name="example-plugin", version="1.0.0"),
        entrypoint="example.metrics:plugin",
    )
    metric = manifest.metrics[0]
    assert metric.entrypoint == "example.metrics:plugin"
    assert metric.inputs["location"].kind == "location"
    assert metric.inputs["value"].model_dump(exclude_none=True) == {
        "kind": "integer",
        "description": "Submitted value.",
        "default": 3,
        "examples": [3],
        "required": False,
        "nullable": False,
        "minimum": 1,
        "maximum": 10,
    }
    assert metric.inputs["mode"].kind == "enum"

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
    assert received["context"] is context
    assert result.data == [[3]]


def test_bounds_and_nested_model_compile_and_parse() -> None:
    plugin = PluginDefinition()
    received: dict[str, Any] = {}

    @plugin.metric(
        name="file_like",
        description="Bounds metric.",
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

    compiled = plugin.compiled_manifest(
        plugin=PluginInfoV3(name="example-plugin", version="1.0.0"),
        entrypoint="example.metrics:plugin",
    )
    schema = compiled.metrics[0].request_schema
    assert any(name.endswith("__YearRange") for name in schema["$defs"])
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
    assert result.data == [[2]]


def test_batch_inputs_generate_contract_and_parse_items() -> None:
    plugin = PluginDefinition()
    received: list[BatchItem[str]] = []
    output = TableOutputV3(
        kind="table",
        batched_columns=[
            BatchedTableOutputColumnV3(
                source="categories",
                name="value_{key}",
                type="integer",
                unit="count",
                description="Value for {label}.",
            )
        ],
    )

    @plugin.metric(
        name="batch_metric",
        description="Batch metric.",
        output=output,
    )
    def calculate(
        location: LocationInput,
        categories: Annotated[
            list[BatchItem[Annotated[str, Field(min_length=2)]]],
            Batch(max_items=3, label=True),
        ],
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

    manifest = plugin.manifest(
        plugin=PluginInfoV3(name="batch-plugin", version="1.0.0"),
        entrypoint="batch.metrics:plugin",
    )
    batch = manifest.metrics[0].inputs["categories"]
    assert batch.kind == "batch"
    assert batch.max_items == 3
    assert batch.label is True

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


@pytest.mark.parametrize(
    ("function", "match"),
    [
        (lambda value: value, "must have an annotation"),
        (lambda *values: values, "positional-or-keyword or keyword-only"),
    ],
)
def test_invalid_metric_signatures_fail_at_registration(
    function: Any,
    match: str,
) -> None:
    plugin = PluginDefinition()
    with pytest.raises(PluginDefinitionError, match=match):
        plugin.metric(
            name="invalid",
            description="Invalid metric.",
            output=_table_output(),
        )(function)


def test_runtime_adapter_rejects_unknown_fields_and_duplicate_batch_keys() -> None:
    plugin = PluginDefinition()

    @plugin.metric(
        name="example",
        description="Example metric.",
        output=_table_output(),
    )
    def calculate(location: LocationInput) -> TableJobResult:
        del location
        raise AssertionError

    with pytest.raises(PluginDefinitionError, match="unexpected input"):
        plugin(
            JobEnvelope(
                job_id="job-1",
                metric="example",
                input={"location": _feature_collection(), "extra": 1},
            ),
            FakeContext(),
        )
