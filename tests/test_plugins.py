from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from lyra.sdk.config import InstalledPluginConfig, normalize_distribution_name
from pydantic import ValidationError

from lyra_app import plugins, registry
from lyra_app.routes import admin
from tests.config_helpers import load_test_config
from tests.plugin_helpers import VERSIONS, plugin_config
from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR

if TYPE_CHECKING:
    from lyra_app.config import LyraConfig


@pytest.fixture
def config(tmp_path: Path) -> LyraConfig:
    return load_test_config(tmp_path, plugins=[SMOKE_PLUGIN_DIR])


@pytest.mark.parametrize(
    ("name", "expected"),
    [("My_Plugin.Name", "my-plugin-name"), ("a", "a"), ("Foo--Bar", "foo-bar")],
)
def test_distribution_names_are_normalized(name: str, expected: str) -> None:
    assert normalize_distribution_name(name) == expected


@pytest.mark.parametrize(
    "name", ["", "a/b", "../escape", " foo", "foo ", "-foo", "foo-", "foo@main"]
)
def test_invalid_distribution_names(name: str) -> None:
    with pytest.raises(ValidationError):
        InstalledPluginConfig(distribution=name)


def test_development_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError, match="absolute"):
        InstalledPluginConfig(
            distribution="plugin", manifest_path=Path("relative.json")
        )


def test_installed_manifest_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugins.sys, "prefix", str(tmp_path))
    path = plugins.installed_manifest_path("Lyra_Smoke.Plugin")
    assert path == tmp_path / "share/lyra/plugins/lyra-smoke-plugin/lyra.plugin.json"
    path.parent.mkdir(parents=True)
    shutil.copyfile(SMOKE_PLUGIN_DIR / "lyra.plugin.json", path)
    VERSIONS["lyra-smoke-plugin"] = "0.1.0"
    manifest = plugins.load_plugin_manifest(
        InstalledPluginConfig(distribution="lyra-smoke-plugin")
    )
    assert manifest.plugin.name == "lyra-smoke-plugin"


def test_missing_distribution_does_not_use_development_manifest() -> None:
    plugin = InstalledPluginConfig(
        distribution="missing-lyra-test-package",
        manifest_path=SMOKE_PLUGIN_DIR / "lyra.plugin.json",
    )
    with pytest.raises(RuntimeError, match="not installed"):
        plugins.load_plugin_manifest(plugin)


@pytest.mark.parametrize("contents", [None, "invalid JSON", '{"schema_version":5}'])
def test_missing_or_invalid_manifest(tmp_path: Path, contents: str | None) -> None:
    VERSIONS["test-plugin"] = "1.0.0"
    path = tmp_path / "lyra.plugin.json"
    if contents is not None:
        path.write_text(contents)
    with pytest.raises(RuntimeError, match="missing, unreadable, or invalid"):
        plugins.load_plugin_manifest(
            InstalledPluginConfig(distribution="test-plugin", manifest_path=path)
        )


@pytest.mark.parametrize("field", ["name", "version"])
def test_manifest_identity_must_match_installed_metadata(
    tmp_path: Path, field: str
) -> None:
    raw = json.loads((SMOKE_PLUGIN_DIR / "lyra.plugin.json").read_text())
    raw["plugin"][field] = "other-plugin" if field == "name" else "9.0.0"
    path = tmp_path / "lyra.plugin.json"
    path.write_text(json.dumps(raw))
    VERSIONS["lyra-smoke-plugin"] = "0.1.0"
    with pytest.raises(RuntimeError, match="identity does not match"):
        plugins.load_plugin_manifest(
            InstalledPluginConfig(distribution="lyra-smoke-plugin", manifest_path=path)
        )


def test_catalog_reads_metadata_without_importing_or_installing(
    config: LyraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_: object, **__: object) -> None:
        pytest.fail(
            "Catalog startup must not import plugin modules or launch subprocesses"
        )

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    registry.initialize_catalog(config)
    registry.ensure_catalog_loaded()
    assert len(registry.get_metrics_info()) == 3


def test_disabled_plugins_need_not_be_installed(config: LyraConfig) -> None:
    config.plugins.installed.append(
        InstalledPluginConfig(
            distribution="missing-disabled", enabled=False, routing={"absent": "batch"}
        )
    )
    loaded = plugins.load_installed_plugins(config.plugins)
    assert [plugin.distribution for plugin in loaded] == ["lyra-smoke-plugin"]
    assert admin.list_plugin_routing().disabled_plugins == ["missing-disabled"]
    assert admin.list_plugins().plugins[-1].version is None


def test_configuration_rejects_duplicate_normalized_distributions(
    config: LyraConfig,
) -> None:
    raw = config.model_dump()
    raw["plugins"]["installed"] = [
        {"distribution": "My_Plugin"},
        {"distribution": "my-plugin"},
    ]
    with pytest.raises(ValidationError, match="unique"):
        type(config).model_validate(raw)


def test_unknown_routes_invalidate_catalog_without_retry(
    config: LyraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry.initialize_catalog(config)
    assert registry.is_catalog_loaded()
    config.plugins.installed[0].routing = {"absent": "batch"}
    registry.initialize_catalog(config)
    assert not registry.is_catalog_loaded()
    assert registry.TASK_REGISTRY == {}
    assert registry.catalog_error()

    def forbidden(_: object) -> None:
        pytest.fail("Catalog reads must not retry initialization")

    monkeypatch.setattr(registry, "load_installed_plugins", forbidden)
    with pytest.raises(registry.CatalogUnavailableError):
        registry.get_metric_catalog()


def test_unknown_queue_rejected(config: LyraConfig) -> None:
    raw = config.model_dump()
    raw["plugins"]["installed"][0]["routing"] = {"smoke_table_metric": "unknown"}
    with pytest.raises(ValidationError, match="allowed_queues"):
        type(config).model_validate(raw)


def test_development_manifest_atomic_replacement_and_catalog_reload(
    tmp_path: Path,
) -> None:
    source = tmp_path / "plugin"
    source.mkdir()
    path = source / "lyra.plugin.json"
    shutil.copyfile(SMOKE_PLUGIN_DIR / "lyra.plugin.json", path)
    config = load_test_config(tmp_path)
    config.plugins.installed = [plugin_config(source)]
    registry.initialize_catalog(config)
    assert len(registry.get_metrics_info()) == 3
    original = registry.get_public_catalog_fingerprint()
    raw = json.loads(path.read_text())
    raw["metrics"] = raw["metrics"][:1]
    temporary = source / "new.json"
    temporary.write_text(json.dumps(raw))
    temporary.replace(path)
    assert len(registry.get_metrics_info()) == 3
    registry.initialize_catalog(config)
    assert len(registry.get_metrics_info()) == 1
    assert registry.get_public_catalog_fingerprint() != original
    config.plugins.installed = []
    registry.initialize_catalog(config)
    assert registry.get_metrics_info() == []


def test_old_configuration_fields_are_rejected(config: LyraConfig) -> None:
    raw = config.model_dump()
    raw["schema_version"] = 2
    with pytest.raises(ValidationError):
        type(config).model_validate(raw)
    raw["schema_version"] = 3
    raw["plugins"]["repos"] = []
    with pytest.raises(ValidationError, match="Extra inputs"):
        type(config).model_validate(raw)
