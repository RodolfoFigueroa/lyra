"""Resolve the installed Lyra application version."""

from importlib.metadata import version

APP_VERSION = version("lyra-app")

__all__ = ["APP_VERSION"]
