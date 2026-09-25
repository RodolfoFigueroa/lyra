"""Installed-distribution metadata doubles for plugin integration tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lyra.sdk.config import InstalledPluginConfig, normalize_distribution_name

if TYPE_CHECKING:
    from pathlib import Path

VERSIONS: dict[str, str] = {}


def plugin_config(
    path: Path, *, routing: dict[str, str] | None = None
) -> InstalledPluginConfig:
    """Register fixture metadata and select a project-root manifest."""
    manifest_path = path / "lyra.plugin.json"
    raw = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    info = raw.get("plugin", {"name": "missing-plugin", "version": "1.0.0"})
    name = normalize_distribution_name(info["name"])
    VERSIONS[name] = info["version"]
    return InstalledPluginConfig(
        distribution=name, manifest_path=manifest_path, routing=routing or {}
    )
