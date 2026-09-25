from importlib import import_module

import pytest
from lyra import api, sdk
from lyra.api.client.async_ import AsyncLyraClient
from lyra.api.client.sync import JobHandle, LyraClient
from lyra.api.exceptions import MetricRunError
from lyra.api.options import RunOptions
from lyra.sdk.context import RunContext
from lyra.sdk.models.job import TableJobResult
from lyra.sdk.models.spatial import MetZoneCode
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.utils.date import get_date_range
from pydantic import ValidationError


def test_client_conveniences_resolve_to_canonical_definitions() -> None:
    assert api.LyraClient is LyraClient
    assert api.AsyncLyraClient is AsyncLyraClient
    assert api.JobHandle is JobHandle
    assert api.RunOptions is RunOptions
    assert api.MetricRunError is MetricRunError
    assert api.parse_result_ref("lyra://results/example") == "example"


def test_plugin_conveniences_resolve_to_canonical_definitions() -> None:
    assert sdk.RunContext is RunContext
    assert sdk.MetricParameters is MetricParameters
    assert sdk.PluginDefinition is PluginDefinition
    assert sdk.metric is metric


def test_models_and_utilities_are_available_from_owning_modules() -> None:
    assert "data" in TableJobResult.model_fields
    assert MetZoneCode(value="09.01").data_type == "met_zone_code"
    assert callable(get_date_range)


@pytest.mark.parametrize(
    ("name", "module"),
    [
        ("RunContext", "context"),
        ("LyraDB", "db"),
        ("Bounds", "db_types"),
        ("MetricParameters", "parameters"),
        ("MetricInputError", "errors"),
        ("MetricResultError", "errors"),
        ("PluginDefinitionError", "errors"),
        ("FileOutput", "models.plugin"),
        ("FractionOfLocationArea", "models.plugin"),
        ("TableColumn", "models.plugin"),
        ("TableOutput", "models.plugin"),
        ("BoundsInput", "plugin"),
        ("LocationInput", "plugin"),
        ("MetricDescription", "plugin"),
        ("PluginDefinition", "plugin"),
        ("metric", "plugin"),
    ],
)
def test_authoring_exports_match_owning_modules(name: str, module: str) -> None:
    assert getattr(sdk, name) is getattr(import_module(f"lyra.sdk.{module}"), name)


@pytest.mark.parametrize("name", ["Input", "BatchInput", "BatchItem", "PluginResult"])
def test_retired_authoring_interfaces_are_absent(name: str) -> None:
    assert not hasattr(sdk, name)
    assert not hasattr(import_module("lyra.sdk.plugin"), name)


def test_manifest_models_have_one_unversioned_interface() -> None:
    models = import_module("lyra.sdk.models.plugin")
    for name in ("PluginInfo", "MetricManifest", "PluginManifest"):
        assert getattr(models, name).__module__ == models.__name__
    with pytest.raises(ModuleNotFoundError, match="plugin_v4"):
        import_module("lyra.sdk.models.plugin_v4")
    assert not hasattr(PluginDefinition, "compiled_manifest")
    for name in ("from_dataframe", "from_series", "from_mapping"):
        assert not hasattr(TableJobResult, name)


@pytest.mark.parametrize(
    ("index", "columns", "data", "message"),
    [
        (["a", "a"], ["value"], [[1], [2]], "index values must be unique"),
        (["a"], ["value", "value"], [[1, 2]], "column values must be unique"),
        (["a", "b"], ["value"], [[1]], "index length must match"),
        (["a"], ["value"], [[1, 2]], "row must match the column count"),
    ],
)
def test_transport_table_validation_survives_constructor_removal(
    index: list[str], columns: list[str], data: list[list[int]], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TableJobResult(job_id="transport", index=index, columns=columns, data=data)
