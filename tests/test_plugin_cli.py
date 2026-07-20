from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from lyra.sdk.plugin_cli import (
    build_manifest,
    check_manifest,
    describe_plugin,
    main,
    render_description,
    render_manifest,
)

from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_project(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        """
[project]
name = "example-plugin"
version = "1.2.3"

[tool.lyra]
plugin = "example_plugin:plugin"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (project / "example_plugin.py").write_text(
        """
from lyra.sdk import Input, LocationInput, PluginDefinition
from lyra.sdk.models import TableJobResult
from lyra.sdk.models.plugin_v3 import TableOutputColumnV3, TableOutputV3

plugin = PluginDefinition()

@plugin.metric(
    name="example",
    description="Example metric.",
    inputs={"value": Input(description="Example input value.")},
    output=TableOutputV3(
        kind="table",
        columns=[TableOutputColumnV3(
            name="value",
            type="integer",
            unit="count",
            description="Example value.",
        )],
    ),
)
def calculate(location: LocationInput, value: int = 2) -> TableJobResult:
    raise AssertionError
""".lstrip(),
        encoding="utf-8",
    )


def test_build_and_check_manifest_are_deterministic(tmp_path: Path) -> None:
    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)

    manifest_path = build_manifest(tmp_path)
    first = manifest_path.read_text(encoding="utf-8")
    second = render_manifest(tmp_path)

    assert first == second
    assert first.endswith("\n")
    payload = json.loads(first)
    assert payload["plugin"] == {"name": "example-plugin", "version": "1.2.3"}
    assert payload["metrics"][0]["entrypoint"] == "example_plugin:plugin"
    assert check_manifest(tmp_path) == (True, "")

    manifest_path.write_text("{}\n", encoding="utf-8")
    valid, diff = check_manifest(tmp_path)
    assert valid is False
    assert "generated:" in diff
    assert manifest_path.read_text(encoding="utf-8") == "{}\n"


def test_cli_exit_codes_and_project_errors(
    tmp_path: Path,
    capsys: object,
) -> None:
    del capsys
    assert main(["check-manifest", "--project", str(tmp_path)]) == 2

    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)
    assert main(["check-manifest", "--project", str(tmp_path)]) == 1
    assert main(["build-manifest", "--project", str(tmp_path)]) == 0
    assert main(["check-manifest", "--project", str(tmp_path)]) == 0


def test_smoke_plugin_manifest_is_current() -> None:
    sys.modules.pop("smoke_plugin.runner", None)
    sys.modules.pop("smoke_plugin", None)

    assert check_manifest(SMOKE_PLUGIN_DIR) == (True, "")


def test_describe_plugin_renders_human_and_json_output(tmp_path: Path) -> None:
    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)

    descriptions = describe_plugin(tmp_path)
    assert [description.name for description in descriptions] == ["example"]
    assert getattr(descriptions[0].inputs["value"], "description", None) == (
        "Example input value."
    )

    human = render_description(tmp_path, "example")
    assert "Metric: example" in human
    assert "Signature: calculate(location: LocationInput, value: int = 2)" in human
    assert "Example input value." in human
    assert "Output: table (1 static column(s), 0 batched column group(s))" in human

    payload = json.loads(render_description(tmp_path, json_output=True))
    assert payload["metrics"][0]["name"] == "example"
    assert payload["metrics"][0]["inputs"]["value"]["description"] == (
        "Example input value."
    )


def test_describe_cli_reports_unknown_metrics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)

    assert main(["describe", "example", "--project", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metrics"][0]["name"] == "example"

    assert main(["describe", "missing", "--project", str(tmp_path)]) == 2
    assert "available metrics: example" in capsys.readouterr().err
