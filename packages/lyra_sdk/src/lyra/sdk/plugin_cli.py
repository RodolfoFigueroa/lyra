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
from typing import TYPE_CHECKING, Any

from lyra.sdk.models.plugin_v3 import (
    BatchInputV3,
    FileOutputV3,
    PluginInfoV3,
    PluginOwnedInputMetadataV3,
    TableOutputV3,
    compile_plugin_manifest,
)
from lyra.sdk.plugin import MetricDescription, PluginDefinition

if TYPE_CHECKING:
    from collections.abc import Sequence

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


def describe_plugin(
    project_root: Path,
    metric_name: str | None = None,
) -> list[MetricDescription]:
    """Load and describe one or all metrics in a plugin project."""

    project_root = project_root.resolve()
    _name, _version, entrypoint = _project_configuration(project_root)
    definition = _load_definition(project_root, entrypoint)
    names = definition.metric_names if metric_name is None else (metric_name,)
    return [definition.describe(name) for name in names]


def render_description(
    project_root: Path,
    metric_name: str | None = None,
    *,
    json_output: bool = False,
) -> str:
    """Render deterministic author-facing metric information."""

    descriptions = describe_plugin(project_root, metric_name)
    if json_output:
        payload = {
            "metrics": [
                description.model_dump(mode="json", exclude_unset=True)
                for description in descriptions
            ]
        }
        return f"{json.dumps(payload, indent=2)}\n"
    rendered = "\n\n".join(_render_metric(description) for description in descriptions)
    return f"{rendered}\n"


def _render_metric(description: MetricDescription) -> str:
    rows = [
        (
            name,
            str(input_spec.kind),
            _input_requirement(input_spec),
            _input_details(input_spec),
            _input_description(input_spec),
        )
        for name, input_spec in description.inputs.items()
    ]
    lines = [
        f"Metric: {description.name}",
        f"Description: {description.description}",
        f"Handler: {description.handler}",
        f"Signature: {description.signature}",
        "Inputs:",
        *_render_table(
            ("Name", "Kind", "Requirement", "Details", "Description"),
            rows,
        ),
        f"Output: {_output_summary(description.output)}",
    ]
    return "\n".join(lines)


def _render_table(
    headers: tuple[str, ...],
    rows: Sequence[tuple[str, ...]],
) -> list[str]:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def render_row(row: tuple[str, ...]) -> str:
        return (
            "  "
            + "  ".join(
                value.ljust(widths[index]) for index, value in enumerate(row)
            ).rstrip()
        )

    return [
        render_row(headers),
        render_row(tuple("-" * width for width in widths)),
        *(render_row(row) for row in rows),
    ]


def _input_requirement(input_spec: Any) -> str:
    if not isinstance(input_spec, PluginOwnedInputMetadataV3):
        return "required"
    if input_spec.required:
        return "required"
    if "default" in input_spec.model_fields_set:
        return f"default={input_spec.default!r}"
    return "optional"


def _input_description(input_spec: Any) -> str:
    if isinstance(input_spec, BatchInputV3):
        return input_spec.value.description or ""
    description = getattr(input_spec, "description", None)
    if isinstance(description, str):
        return description
    if input_spec.kind == "location":
        return "Lyra-resolved locations."
    return "Lyra-resolved bounds."


def _input_details(input_spec: Any) -> str:
    if isinstance(input_spec, BatchInputV3):
        labels = "allowed" if input_spec.label else "disabled"
        return (
            f"items={input_spec.value.kind}, max_items={input_spec.max_items}, "
            f"labels={labels}"
        )
    details: list[str] = []
    if getattr(input_spec, "nullable", False):
        details.append("nullable")
    values = getattr(input_spec, "values", None)
    if isinstance(values, list):
        details.append(f"values={values!r}")
    for field in (
        "minimum",
        "maximum",
        "min_length",
        "max_length",
        "pattern",
    ):
        value = getattr(input_spec, field, None)
        if value is not None:
            details.append(f"{field}={value!r}")
    schema = getattr(input_spec, "schema", None)
    if isinstance(schema, dict):
        schema_fields = (
            "exclusiveMinimum",
            "minimum",
            "exclusiveMaximum",
            "maximum",
            "multipleOf",
            "minLength",
            "maxLength",
            "pattern",
            "minItems",
            "maxItems",
        )
        details.extend(
            f"{field}={schema[field]!r}" for field in schema_fields if field in schema
        )
    return ", ".join(details) or "—"


def _output_summary(output: Any) -> str:
    if isinstance(output, TableOutputV3):
        return (
            f"table ({len(output.columns)} static column(s), "
            f"{len(output.batched_columns)} batched column group(s))"
        )
    if isinstance(output, FileOutputV3):
        extensions = ", ".join(output.extensions)
        return f"file ({output.media_type}; {extensions})"
    return str(output.kind)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyra-plugin",
        description="Build, verify, and inspect Lyra plugin definitions.",
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
    describe_parser = subparsers.add_parser("describe")
    describe_parser.add_argument(
        "metric",
        nargs="?",
        help="Metric name (default: describe every registered metric).",
    )
    describe_parser.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="Plugin project root (default: current directory).",
    )
    describe_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit structured JSON instead of a human-readable table.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build-manifest":
            path = build_manifest(args.project)
            sys.stdout.write(f"{path}\n")
            return 0
        if args.command == "describe":
            sys.stdout.write(
                render_description(
                    args.project,
                    args.metric,
                    json_output=args.json_output,
                )
            )
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
    "describe_plugin",
    "main",
    "render_description",
    "render_manifest",
]
