from collections.abc import Iterator
from importlib import metadata

import pytest

from tests.plugin_helpers import VERSIONS

pytest_plugins = ["tests.rq_helpers"]


@pytest.fixture(autouse=True)
def installed_plugin_metadata(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Supply metadata only for explicitly registered fixture distributions."""
    original = metadata.version
    VERSIONS.clear()

    def version(name: str) -> str:
        if name in VERSIONS:
            return VERSIONS[name]
        return original(name)

    monkeypatch.setattr(metadata, "version", version)
    yield
    VERSIONS.clear()
