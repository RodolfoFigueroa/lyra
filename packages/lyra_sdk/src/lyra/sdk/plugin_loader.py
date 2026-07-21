from __future__ import annotations

import importlib
import inspect
import re

from lyra.sdk.plugin import PluginDefinition

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_FACTORY_PATTERN = re.compile(
    rf"^(?P<module>{_IDENTIFIER}(?:\.{_IDENTIFIER})*):(?P<attribute>{_IDENTIFIER})$"
)


class PluginLoadError(RuntimeError):
    """Raised when a configured plugin factory cannot produce a definition."""


def load_plugin_definition(factory_ref: str) -> PluginDefinition:
    """Import and invoke one synchronous, parameterless plugin factory."""
    match = _FACTORY_PATTERN.fullmatch(factory_ref)
    if match is None:
        msg = f"Plugin factory must use 'module:attribute' format: {factory_ref!r}"
        raise PluginLoadError(msg)

    try:
        module = importlib.import_module(match.group("module"))
        factory = getattr(module, match.group("attribute"))
    except (ImportError, AttributeError) as exc:
        msg = f"Could not import plugin factory {factory_ref!r}: {exc}"
        raise PluginLoadError(msg) from exc

    if not callable(factory):
        msg = f"Plugin factory {factory_ref!r} must resolve to a callable"
        raise PluginLoadError(msg)
    if inspect.iscoroutinefunction(factory):
        msg = f"Plugin factory {factory_ref!r} must be synchronous"
        raise PluginLoadError(msg)
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError) as exc:
        msg = f"Could not inspect plugin factory {factory_ref!r}: {exc}"
        raise PluginLoadError(msg) from exc
    if signature.parameters:
        msg = f"Plugin factory {factory_ref!r} must declare no parameters"
        raise PluginLoadError(msg)

    try:
        definition = factory()
    except Exception as exc:
        msg = f"Plugin factory {factory_ref!r} failed: {exc}"
        raise PluginLoadError(msg) from exc
    if not isinstance(definition, PluginDefinition):
        msg = f"Plugin factory {factory_ref!r} must return PluginDefinition"
        raise PluginLoadError(msg)
    return definition


__all__ = ["PluginLoadError", "load_plugin_definition"]
