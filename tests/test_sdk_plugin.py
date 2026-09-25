from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import pandas as pd
import pytest
from jsonschema import Draft202012Validator
from lyra.sdk import MetricInputError, PluginDefinitionError, RunContext
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.job import FileJobResult, JobEnvelope, TableJobResult
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from lyra.sdk.plugin_cli import check_manifest, render_manifest
from lyra.sdk.schema import resolve_schema_reference, schema_nodes

from tests.fixtures.contract_plugin import metrics
from tests.fixtures.contract_plugin.plugin import create_plugin

if TYPE_CHECKING:
    from lyra.sdk import PluginDefinition


@pytest.fixture
def plugin() -> PluginDefinition:
    return create_plugin()


@pytest.fixture
def location() -> GeoJSON:
    return GeoJSON.model_validate(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": name,
                    "geometry": {"type": "Point", "coordinates": [-99.1, 19.4]},
                    "properties": {},
                }
                for name in ["area_a", "area_b"]
            ],
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        }
    )


def test_six_specimens_and_direct_calls(
    plugin: PluginDefinition, location: GeoJSON
) -> None:
    cases = [
        ("capacity", {}, [[10], [10]]),
        ("reused_capacity", {}, [[4], [4]]),
        (
            "selection_size",
            {"selection": {"activity_codes": ["a", "b"]}, "threshold": None},
            [[2], [2]],
        ),
        ("interval_width", {"lower": 2, "upper": 5}, [[3], [3]]),
    ]
    for name, values, expected in cases:
        parameters = plugin.prepare_parameters(name, values)
        frame = getattr(metrics, name)(parameters=parameters, location=location)
        assert isinstance(frame, pd.DataFrame)
        result = plugin.normalize_result(name, frame, job_id="local", location=location)
        assert isinstance(result, TableJobResult)
        assert result.data == expected
        assert result.index == ["area_a", "area_b"]
    assert metrics.ExistingSettings.model_config == {}
    assert (
        metrics.ExistingSettings.model_validate({"units": 4, "ignored": True}).units
        == 4
    )
    bounds = SingleGeoJSON.model_validate(
        location.model_copy(update={"features": location.features[:1]}).model_dump()
    )
    frame = metrics.bounded_features(location, bounds)
    result = plugin.normalize_result(
        "bounded_features", frame, job_id="local", location=location
    )
    assert isinstance(result, TableJobResult)
    assert result.data == [["Point"], ["Point"]]


def test_file_specimen(
    plugin: PluginDefinition, location: GeoJSON, tmp_path: Path
) -> None:
    context = create_autospec(RunContext, instance=True)
    context.temp_dir = tmp_path
    context.logger = logging.getLogger(__name__)
    path = metrics.feature_report(location, context)
    result = plugin.normalize_result(
        "feature_report", path, job_id="local", temp_dir=tmp_path
    )
    assert isinstance(result, FileJobResult)
    assert Path(result.file_path).read_text(encoding="utf-8") == "area_a\narea_b\n"
    assert result.media_type == "text/plain"
    with pytest.raises(PluginDefinitionError, match="no parameters"):
        plugin.prepare_parameters("feature_report", {})


def test_all_documented_requests(plugin: PluginDefinition) -> None:
    text = Path("docs/design/plugin-contract/examples.md").read_text(encoding="utf-8")
    requests = [
        json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.DOTALL)
    ]
    expected = [True, True, False, False, False, False, True, True, True, True, True]
    manifest = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="example:create_plugin"
    )
    schemas = {item.name: item.request_schema for item in manifest.metrics}
    for request, valid in zip(requests[:11], expected, strict=True):
        validator = Draft202012Validator(schemas[request["metric"]])
        assert validator.is_valid(request["input"]) is valid, request
    # E6's semantic condition deliberately is not part of request JSON Schema.
    with pytest.raises(MetricInputError, match="lower must not exceed upper"):
        plugin.prepare_parameters("interval_width", {"lower": 10, "upper": 3})


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"units": "5"},
        {"units": True},
        {"units": 0},
        {"extra": 1},
        {"units": float("inf")},
    ],
)
def test_parameter_failures_have_paths(plugin: PluginDefinition, value: object) -> None:
    with pytest.raises(MetricInputError, match=r"capacity.*parameters"):
        plugin.prepare_parameters("capacity", value)


def test_nested_parameters_and_json_integer(plugin: PluginDefinition) -> None:
    assert plugin.prepare_parameters("capacity", {"units": 5.0}).model_dump() == {
        "units": 5
    }
    for value in [
        {"selection": {"activity_codes": ["a"]}},
        {"selection": {"activity_codes": []}, "threshold": None},
        {"selection": {"activity_codes": [1]}, "threshold": None},
        {"selection": {"activity_codes": ["a"], "extra": 1}, "threshold": None},
    ]:
        with pytest.raises(MetricInputError):
            plugin.prepare_parameters("selection_size", value)


def test_manifest_roundtrip_refs_and_cli(
    plugin: PluginDefinition, tmp_path: Path
) -> None:
    manifest = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="example:create_plugin"
    )
    assert manifest.schema_version == 5
    assert [item.name for item in manifest.metrics] == sorted(plugin.metric_names)
    assert PluginManifest.model_validate_json(manifest.model_dump_json()) == manifest
    for item in manifest.metrics:
        for _, node in schema_nodes(item.request_schema):
            if "$ref" in node:
                resolve_schema_reference(item.request_schema, node["$ref"])
        output = item.output.model_dump(mode="json")
        assert "kind" in output
        for column in output.get("columns", []):
            assert column["nullable"] is False
            assert "derivations" not in column
    project = Path("tests/fixtures/contract_plugin").resolve()
    first = render_manifest(project)
    assert render_manifest(project) == first
    (tmp_path / "pyproject.toml").write_text(
        (project / "pyproject.toml").read_text(encoding="utf-8")
    )
    (tmp_path / "lyra.plugin.json").write_text(first)
    assert check_manifest(tmp_path) == (True, "")


def test_resolved_invocation_and_input_keys(
    plugin: PluginDefinition, location: GeoJSON
) -> None:
    context = create_autospec(RunContext, instance=True)
    job = JobEnvelope(
        job_id="j",
        metric="capacity",
        input={"location": location.model_dump(), "parameters": {}},
    )
    frame = plugin(job, context)
    assert isinstance(frame, pd.DataFrame)
    assert frame["capacity"].tolist() == [10, 10]
    assert job.input["parameters"] == {}
    for inputs in [
        {"location": location.model_dump()},
        {**job.input, "extra": 1},
        {"location": {}, "parameters": {}},
    ]:
        with pytest.raises(MetricInputError):
            plugin(job.model_copy(update={"input": inputs}), context)
