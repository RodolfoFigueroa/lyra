from __future__ import annotations

from types import SimpleNamespace

import pytest
from lyra.sdk import LocationInput, PluginDefinition, metric
from lyra.sdk.models.plugin_v4 import TableOutputColumnV4, TableOutputV4
from lyra.sdk.plugin_loader import PluginLoadError, load_plugin_definition


def _output() -> TableOutputV4:
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


@metric(name="example", description="Example.", output=_output())
def _handler(location: LocationInput) -> object:
    raise AssertionError(location)


def _definition() -> PluginDefinition:
    return PluginDefinition(metrics=[_handler])


def _install_factory(
    monkeypatch: pytest.MonkeyPatch,
    factory: object,
) -> None:
    monkeypatch.setattr(
        "lyra.sdk.plugin_loader.importlib.import_module",
        lambda _module: SimpleNamespace(create_plugin=factory),
    )


def test_loader_returns_fresh_factory_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_factory(monkeypatch, _definition)

    first = load_plugin_definition("example.plugin:create_plugin")
    second = load_plugin_definition("example.plugin:create_plugin")

    assert first.metric_names == ("example",)
    assert second.metric_names == ("example",)
    assert first is not second


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (42, "must resolve to a callable"),
        (lambda value: value, "must declare no parameters"),
        (object, "must return PluginDefinition"),
    ],
)
def test_loader_rejects_invalid_factories(
    monkeypatch: pytest.MonkeyPatch,
    factory: object,
    match: str,
) -> None:
    _install_factory(monkeypatch, factory)

    with pytest.raises(PluginLoadError, match=match):
        load_plugin_definition("example.plugin:create_plugin")


def test_loader_rejects_async_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create_plugin() -> PluginDefinition:
        return _definition()

    _install_factory(monkeypatch, create_plugin)

    with pytest.raises(PluginLoadError, match="must be synchronous"):
        load_plugin_definition("example.plugin:create_plugin")


def test_loader_wraps_factory_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def create_plugin() -> PluginDefinition:
        msg = "factory exploded"
        raise ValueError(msg)

    _install_factory(monkeypatch, create_plugin)

    with pytest.raises(PluginLoadError, match="factory exploded"):
        load_plugin_definition("example.plugin:create_plugin")


@pytest.mark.parametrize(
    "factory_ref",
    ["example.plugin", "example.plugin:create:again", "bad-name:create_plugin"],
)
def test_loader_rejects_malformed_references(factory_ref: str) -> None:
    with pytest.raises(PluginLoadError, match="module:attribute"):
        load_plugin_definition(factory_ref)
