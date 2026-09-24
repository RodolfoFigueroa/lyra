"""Helpers to start a real catalog using test-owned directory sources."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lyra.sdk.config import PluginRepoConfig

from lyra_app import registry
from lyra_app.config import get_config

if TYPE_CHECKING:
    from lyra_app.plugins import PluginLocation


def configure_catalog_sources(sources: list[PluginLocation]) -> None:
    config = get_config()
    overrides = {
        name: queue
        for repo in config.plugins.repos
        for name, queue in repo.routing.items()
    }
    repos = []
    for source in sources:
        raw = json.loads((source.path / "lyra.plugin.json").read_text())
        names = {metric.get("name") for metric in raw.get("metrics", [])}
        repos.append(
            PluginRepoConfig(
                id=source.repo_id,
                source=f"dir://{source.path}",
                routing={
                    name: queue for name, queue in overrides.items() if name in names
                },
            )
        )
    config.plugins.repos = repos
    registry.initialize_catalog(config)


def restart_catalog() -> str:
    registry.initialize_catalog()
    registry.ensure_catalog_loaded()
    return registry.get_loaded_catalog_fingerprint()
