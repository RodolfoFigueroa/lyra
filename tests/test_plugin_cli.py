from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from lyra.sdk import plugin_cli
from lyra.sdk.plugin_cli import (
    PRE_COMMIT_HOOK_ID,
    add_pre_commit_hook,
    build_manifest,
    check_manifest,
    describe_plugin,
    main,
    render_description,
    render_manifest,
)
from pre_commit.clientlib import load_config
from ruamel.yaml import YAML

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
factory = "example_plugin:create_plugin"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (project / "example_plugin.py").write_text(
        """
from lyra.sdk import Input, LocationInput, PluginDefinition, metric
from lyra.sdk.models import TableJobResult
from lyra.sdk.models.plugin_v4 import TableOutputColumnV4, TableOutputV4

@metric(
    name="example",
    description="Example metric.",
    inputs={"value": Input(description="Example input value.")},
    output=TableOutputV4(
        kind="table",
        columns=[TableOutputColumnV4(
            name="value",
            type="integer",
            unit="count",
            description="Example value.",
        )],
    ),
)
def calculate(location: LocationInput, value: int = 2) -> TableJobResult:
    raise AssertionError

def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[calculate])
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
    assert payload["factory"] == "example_plugin:create_plugin"
    assert "entrypoint" not in payload["metrics"][0]
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


def test_add_pre_commit_hook_creates_valid_configuration(tmp_path: Path) -> None:
    config_path, added = add_pre_commit_hook(tmp_path)

    assert added is True
    assert config_path == tmp_path / ".pre-commit-config.yaml"
    load_config(str(config_path))

    configuration = YAML(typ="safe").load(config_path.read_text(encoding="utf-8"))
    hook = configuration["repos"][0]["hooks"][0]
    assert hook == {
        "id": PRE_COMMIT_HOOK_ID,
        "name": "Build and validate Lyra plugin manifest",
        "entry": "uv run lyra-plugin build-manifest",
        "language": "system",
        "pass_filenames": False,
        "always_run": True,
    }


def test_add_pre_commit_hook_preserves_existing_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / ".pre-commit-config.yaml"
    config_path.write_text(
        """# Keep this comment.
repos:
  - repo: https://example.com/hooks
    rev: v1
    hooks:
      - id: remote-hook
  - repo: local
    hooks:
      - id: custom-hook
        name: Custom hook
        entry: "echo hello"
        language: system
""",
        encoding="utf-8",
    )

    _path, added = add_pre_commit_hook(tmp_path)
    content = config_path.read_text(encoding="utf-8")

    assert added is True
    assert content.startswith("# Keep this comment.\n")
    assert 'entry: "echo hello"' in content
    configuration = YAML(typ="safe").load(content)
    assert len(configuration["repos"]) == 2
    assert [hook["id"] for hook in configuration["repos"][1]["hooks"]] == [
        "custom-hook",
        PRE_COMMIT_HOOK_ID,
    ]


def test_add_pre_commit_hook_adds_local_repository_to_remote_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".pre-commit-config.yaml"
    config_path.write_text(
        """repos:
  - repo: https://example.com/hooks
    rev: v1
    hooks:
      - id: remote-hook
""",
        encoding="utf-8",
    )

    add_pre_commit_hook(tmp_path)

    configuration = YAML(typ="safe").load(config_path.read_text(encoding="utf-8"))
    assert [repo["repo"] for repo in configuration["repos"]] == [
        "https://example.com/hooks",
        "local",
    ]


def test_add_pre_commit_hook_leaves_customized_existing_hook_unchanged(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".pre-commit-config.yaml"
    content = f"""repos:
  - repo: local
    hooks:
      - id: {PRE_COMMIT_HOOK_ID}
        name: My custom manifest hook
        entry: custom-command
        language: system
"""
    config_path.write_text(content, encoding="utf-8")

    assert add_pre_commit_hook(tmp_path) == (config_path, False)
    assert config_path.read_text(encoding="utf-8") == content


def test_add_pre_commit_hook_rejects_invalid_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / ".pre-commit-config.yaml"
    content = "repos: not-a-list\n"
    config_path.write_text(content, encoding="utf-8")

    assert main(["add-pre-commit-hook", "--project", str(tmp_path)]) == 2
    assert config_path.read_text(encoding="utf-8") == content


def test_add_pre_commit_hook_rejects_malformed_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / ".pre-commit-config.yaml"
    content = "repos: [\n"
    config_path.write_text(content, encoding="utf-8")

    assert main(["add-pre-commit-hook", "--project", str(tmp_path)]) == 2
    assert config_path.read_text(encoding="utf-8") == content


def test_add_pre_commit_hook_populates_empty_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / ".pre-commit-config.yaml"
    config_path.write_text("", encoding="utf-8")

    _path, added = add_pre_commit_hook(tmp_path)

    assert added is True
    load_config(str(config_path))


def test_add_pre_commit_hook_does_not_write_after_atomic_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / ".pre-commit-config.yaml"
    content = "repos: []\n"
    config_path.write_text(content, encoding="utf-8")

    def fail_write(_path: Path, _content: str) -> None:
        message = "write failed"
        raise OSError(message)

    monkeypatch.setattr(plugin_cli, "_write_atomic", fail_write)

    assert main(["add-pre-commit-hook", "--project", str(tmp_path)]) == 2
    assert config_path.read_text(encoding="utf-8") == content


def test_add_pre_commit_hook_cli_reports_added_and_existing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = ["add-pre-commit-hook", "--project", str(tmp_path)]

    assert main(arguments) == 0
    assert "Added Lyra plugin manifest hook to" in capsys.readouterr().out
    assert main(arguments) == 0
    assert "already exists in" in capsys.readouterr().out


def test_smoke_plugin_manifest_is_current() -> None:
    sys.modules.pop("smoke_plugin.metrics", None)
    sys.modules.pop("smoke_plugin.plugin", None)
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
