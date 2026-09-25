"""Helpers to start a real catalog using installed plugin metadata doubles."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lyra_app import registry
from lyra_app.config import get_config
from tests.plugin_helpers import plugin_config

if TYPE_CHECKING:
    from pathlib import Path


def configure_catalog_plugins(paths: list[Path]) -> None:
    config = get_config()
    overrides = {
        name: queue
        for plugin in config.plugins.installed
        for name, queue in plugin.routing.items()
    }
    plugins = []
    for path in paths:
        raw = json.loads((path / "lyra.plugin.json").read_text())
        names = {metric.get("name") for metric in raw.get("metrics", [])}
        plugins.append(
            plugin_config(
                path,
                routing={
                    name: queue for name, queue in overrides.items() if name in names
                },
            )
        )
    config.plugins.installed = plugins
    registry.initialize_catalog(config)


def restart_catalog() -> str:
    registry.initialize_catalog()
    registry.ensure_catalog_loaded()
    return registry.get_loaded_catalog_fingerprint()
