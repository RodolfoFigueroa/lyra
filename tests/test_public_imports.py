from lyra import api, sdk
from lyra.api.client.async_ import AsyncLyraClient
from lyra.api.client.sync import JobHandle, LyraClient
from lyra.api.exceptions import MetricRunError
from lyra.api.options import RunOptions
from lyra.sdk.context import RunContext
from lyra.sdk.models.job import TableJobResult
from lyra.sdk.models.spatial import MetZoneCode
from lyra.sdk.plugin import Input, PluginDefinition, metric
from lyra.utils.date import get_date_range


def test_client_conveniences_resolve_to_canonical_definitions() -> None:
    assert api.LyraClient is LyraClient
    assert api.AsyncLyraClient is AsyncLyraClient
    assert api.JobHandle is JobHandle
    assert api.RunOptions is RunOptions
    assert api.MetricRunError is MetricRunError
    assert api.parse_result_ref("lyra://results/example") == "example"


def test_plugin_conveniences_resolve_to_canonical_definitions() -> None:
    assert sdk.RunContext is RunContext
    assert sdk.Input is Input
    assert sdk.PluginDefinition is PluginDefinition
    assert sdk.metric is metric


def test_models_and_utilities_are_available_from_owning_modules() -> None:
    assert "data" in TableJobResult.model_fields
    assert MetZoneCode(value="09.01").data_type == "met_zone_code"
    assert callable(get_date_range)
