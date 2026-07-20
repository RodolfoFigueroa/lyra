from __future__ import annotations

import argparse
import difflib
import importlib
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path

from lyra.sdk.models.plugin_v3 import PluginInfoV3, compile_plugin_manifest
from lyra.sdk.plugin import PluginDefinition

MANIFEST_FILENAME = "lyra.plugin.json"


class PluginBuildError(RuntimeError):
    """Raised when project metadata cannot produce a plugin manifest."""


def _project_configuration(project_root: Path) -> tuple[str, str, str]:
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.is_file():
        msg = f"Plugin project is missing {pyproject_path}"
        raise PluginBuildError(msg)
    with pyproject_path.open("rb") as pyproject_file:
        payload = tomllib.load(pyproject_file)

    project = payload.get("project")
    if not isinstance(project, dict):
        msg = "pyproject.toml must contain a [project] table"
        raise PluginBuildError(msg)
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        msg = "[project].name must be a non-empty string"
        raise PluginBuildError(msg)
    if not isinstance(version, str) or not version:
        msg = "[project].version must be a static non-empty string"
        raise PluginBuildError(msg)

    tool = payload.get("tool")
    lyra = tool.get("lyra") if isinstance(tool, dict) else None
    entrypoint = lyra.get("plugin") if isinstance(lyra, dict) else None
    if not isinstance(entrypoint, str) or not entrypoint:
        msg = "pyproject.toml must define [tool.lyra].plugin as 'module:object'"
        raise PluginBuildError(msg)
    return name, version, entrypoint


def _load_definition(project_root: Path, entrypoint: str) -> PluginDefinition:
    module_name, separator, object_name = entrypoint.partition(":")
    if not separator or not module_name or not object_name:
        msg = f"Plugin entrypoint must use 'module:object' format: {entrypoint!r}"
        raise PluginBuildError(msg)

    import_paths = [project_root, project_root / "src"]
    for import_path in reversed(import_paths):
        path = str(import_path)
        if import_path.is_dir() and path not in sys.path:
            sys.path.insert(0, path)
    try:
        value = getattr(importlib.import_module(module_name), object_name)
    except (ImportError, AttributeError) as exc:
        msg = f"Could not import plugin definition {entrypoint!r}: {exc}"
        raise PluginBuildError(msg) from exc
    if not isinstance(value, PluginDefinition):
        msg = f"Plugin entrypoint {entrypoint!r} must resolve to PluginDefinition"
        raise PluginBuildError(msg)
    return value


def render_manifest(project_root: Path) -> str:
    """Build the canonical manifest text for one plugin project."""

    project_root = project_root.resolve()
    name, version, entrypoint = _project_configuration(project_root)
    definition = _load_definition(project_root, entrypoint)
    manifest = definition.manifest(
        plugin=PluginInfoV3(name=name, version=version),
        entrypoint=entrypoint,
    )
    compile_plugin_manifest(manifest)
    return (
        json.dumps(
            manifest.model_dump(mode="json", exclude_unset=True),
            indent=2,
        )
        + "\n"
    )


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(content)
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def build_manifest(project_root: Path) -> Path:
    content = render_manifest(project_root)
    manifest_path = project_root.resolve() / MANIFEST_FILENAME
    if (
        not manifest_path.is_file()
        or manifest_path.read_text(encoding="utf-8") != content
    ):
        _write_atomic(manifest_path, content)
    return manifest_path


def check_manifest(project_root: Path) -> tuple[bool, str]:
    expected = render_manifest(project_root)
    manifest_path = project_root.resolve() / MANIFEST_FILENAME
    actual = (
        manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else ""
    )
    if actual == expected:
        return True, ""
    diff = "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(manifest_path),
            tofile=f"generated:{manifest_path}",
        )
    )
    return False, diff


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyra-plugin",
        description="Build and verify generated Lyra plugin manifests.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build-manifest", "check-manifest"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "--project",
            type=Path,
            default=Path.cwd(),
            help="Plugin project root (default: current directory).",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build-manifest":
            path = build_manifest(args.project)
            sys.stdout.write(f"{path}\n")
            return 0
        valid, diff = check_manifest(args.project)
    except (PluginBuildError, OSError, ValueError) as exc:
        sys.stderr.write(f"lyra-plugin: {exc}\n")
        return 2
    if valid:
        return 0
    sys.stderr.write(diff)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PluginBuildError",
    "build_manifest",
    "build_parser",
    "check_manifest",
    "main",
    "render_manifest",
]
