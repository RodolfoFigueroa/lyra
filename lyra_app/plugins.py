"""Read installed plugin metadata and manifests without importing plugin code."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.sdk.config import normalize_distribution_name
from lyra.sdk.models.plugin import PluginManifest

if TYPE_CHECKING:
    from lyra.sdk.config import InstalledPluginConfig, PluginsConfig

MANIFEST_FILENAME = "lyra.plugin.json"


@dataclass(frozen=True)
class InstalledPlugin:
    """Pair a selected distribution with its validated manifest and routing."""

    distribution: str
    manifest: PluginManifest
    metric_queues: dict[str, str]


def installed_version(distribution: str) -> str | None:
    """Return installed distribution metadata without importing its modules."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def installed_manifest_path(distribution: str) -> Path:
    """Return the conventional manifest path within the current environment."""
    return (
        Path(sys.prefix)
        / "share"
        / "lyra"
        / "plugins"
        / normalize_distribution_name(distribution)
        / MANIFEST_FILENAME
    )


def load_plugin_manifest(plugin: InstalledPluginConfig) -> PluginManifest:
    """Read a manifest and verify its installed distribution identity.

    Returns:
        The validated manifest, without importing its factory.

    Raises:
        RuntimeError: If the package or manifest is missing or inconsistent.
    """
    version = installed_version(plugin.distribution)
    if version is None:
        msg = f"Plugin distribution {plugin.distribution!r} is not installed."
        raise RuntimeError(msg)
    path = plugin.manifest_path or installed_manifest_path(plugin.distribution)
    try:
        manifest = PluginManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = f"Plugin manifest {path} is missing, unreadable, or invalid: {exc}"
        raise RuntimeError(msg) from exc
    if (
        normalize_distribution_name(manifest.plugin.name) != plugin.distribution
        or manifest.plugin.version != version
    ):
        msg = (
            f"Plugin manifest {path} identity does not match installed distribution "
            f"{plugin.distribution!r} version {version!r}."
        )
        raise RuntimeError(msg)
    return manifest


def load_installed_plugins(config: PluginsConfig) -> list[InstalledPlugin]:
    """Load enabled manifests and resolve their queue assignments.

    Returns:
        Validated installed plugins in configuration order.

    Raises:
        ValueError: If distributions, metrics, or queue overrides conflict.
    """
    plugins: list[InstalledPlugin] = []
    distributions: set[str] = set()
    metric_names: set[str] = set()
    for plugin in config.installed:
        if plugin.distribution in distributions:
            msg = f"Duplicate plugin distribution: {plugin.distribution!r}"
            raise ValueError(msg)
        distributions.add(plugin.distribution)
        if not plugin.enabled:
            continue
        manifest = load_plugin_manifest(plugin)
        names = {metric.name for metric in manifest.metrics}
        duplicates = metric_names & names
        if duplicates:
            msg = "Duplicate metric names in plugin manifests: " + ", ".join(
                sorted(duplicates)
            )
            raise ValueError(msg)
        unknown = set(plugin.routing) - names
        if unknown:
            msg = (
                f"Plugin {plugin.distribution!r} has routing overrides "
                "for missing metrics: " + ", ".join(sorted(unknown))
            )
            raise ValueError(msg)
        queues = {
            name: plugin.routing.get(name, config.default_queue) for name in names
        }
        if set(queues.values()) - set(config.allowed_queues):
            msg = "Plugin routing queues must appear in plugins.allowed_queues"
            raise ValueError(msg)
        metric_names.update(names)
        plugins.append(InstalledPlugin(plugin.distribution, manifest, queues))
    return plugins
