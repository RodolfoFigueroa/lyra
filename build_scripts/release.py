"""Validate and plan aggregate Lyra releases from Release Please manifests."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = "release-please-config.json"
MANIFEST_PATH = ".release-please-manifest.json"
PRODUCT_PATH = "."
PRODUCT_COMPONENT = "lyra"
PRODUCT_PACKAGE = "lyra-app"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
COMPONENT_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
INTERNAL_DEPENDENCIES = {
    ".": ("lyra-sdk", "lyra-utils"),
    "packages/lyra_api": ("lyra-sdk",),
    "packages/lyra_tui": ("lyra-api",),
    "packages/lyra_utils": ("lyra-sdk",),
}


class ReleasePlanError(ValueError):
    """Raised when repository release state is incomplete or inconsistent."""


@dataclass(frozen=True)
class PackageConfig:
    """Release Please configuration needed by the aggregate publisher."""

    path: str
    component: str
    package_name: str
    changelog_path: str

    @property
    def pyproject_path(self) -> str:
        """Return the package's project metadata path."""
        if self.path == PRODUCT_PATH:
            return "pyproject.toml"
        return f"{self.path}/pyproject.toml"

    @property
    def repository_changelog_path(self) -> str:
        """Return the changelog path relative to the repository root."""
        if self.path == PRODUCT_PATH:
            return self.changelog_path
        return f"{self.path}/{self.changelog_path}"


@dataclass(frozen=True)
class ComponentRelease:
    """One component included in an aggregate Lyra release."""

    name: str
    path: str
    version: str
    tag: str
    changed: bool


@dataclass(frozen=True)
class ReleasePlan:
    """Files and GitHub Actions outputs generated for a release."""

    product_version: str
    product_tag: str
    changed_components: tuple[ComponentRelease, ...]
    notes_path: Path
    manifest_path: Path


def _object_dict(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict):
        message = f"{description} must be a JSON/TOML object"
        raise ReleasePlanError(message)
    return cast("dict[str, object]", value)


def _string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value:
        message = f"{description} must be a non-empty string"
        raise ReleasePlanError(message)
    return value


def _json_object(text: str, description: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        message = f"{description} is not valid JSON: {error}"
        raise ReleasePlanError(message) from error
    return _object_dict(value, description)


def _toml_object(text: str, description: str) -> dict[str, object]:
    try:
        value = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        message = f"{description} is not valid TOML: {error}"
        raise ReleasePlanError(message) from error
    return _object_dict(value, description)


def _repository_text(root: Path, ref: str, path: str) -> str:
    git = shutil.which("git")
    if git is None:
        message = "git is required to plan a release"
        raise ReleasePlanError(message)
    result = subprocess.run(  # noqa: S603 -- ref is validated as a commit SHA
        [git, "show", f"{ref}:{path}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = f"Could not read {path} at {ref}: {result.stderr.strip()}"
        raise ReleasePlanError(message)
    return result.stdout


def _repository_sha(root: Path, ref: str) -> str:
    git = shutil.which("git")
    if git is None:
        message = "git is required to plan a release"
        raise ReleasePlanError(message)
    result = subprocess.run(  # noqa: S603 -- ref is validated as a commit SHA
        [git, "rev-parse", f"{ref}^{{commit}}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = f"Could not resolve release ref {ref}: {result.stderr.strip()}"
        raise ReleasePlanError(message)
    return result.stdout.strip()


def load_package_configs(config_text: str) -> tuple[PackageConfig, ...]:
    """Load and validate the package map from Release Please configuration."""
    config = _json_object(config_text, CONFIG_PATH)
    if config.get("skip-github-release") is not True:
        message = "Release Please must run with skip-github-release enabled"
        raise ReleasePlanError(message)
    packages = _object_dict(config.get("packages"), "packages")
    parsed: list[PackageConfig] = []
    for path, raw_package in packages.items():
        package = _object_dict(raw_package, f"package {path}")
        component = _string(package.get("component"), f"{path} component")
        if COMPONENT_PATTERN.fullmatch(component) is None:
            message = f"{path} component is not a safe tag prefix: {component}"
            raise ReleasePlanError(message)
        parsed.append(
            PackageConfig(
                path=path,
                component=component,
                package_name=_string(
                    package.get("package-name"), f"{path} package-name"
                ),
                changelog_path=cast(
                    "str", package.get("changelog-path", "CHANGELOG.md")
                ),
            )
        )
    if not parsed or parsed[0].path != PRODUCT_PATH:
        message = "The root product package must be the first configured package"
        raise ReleasePlanError(message)
    root_package = parsed[0]
    if (
        root_package.component != PRODUCT_COMPONENT
        or root_package.package_name != PRODUCT_PACKAGE
    ):
        message = "The root package must publish the lyra product from lyra-app"
        raise ReleasePlanError(message)
    root_config = _object_dict(packages[PRODUCT_PATH], "root package")
    exclusions = root_config.get("exclude-paths", [])
    if exclusions != ["docs/**"]:
        message = "The product release may exclude only docs/**"
        raise ReleasePlanError(message)
    return tuple(parsed)


def load_versions(manifest_text: str) -> dict[str, str]:
    """Load strict package versions from a Release Please manifest."""
    manifest = _json_object(manifest_text, MANIFEST_PATH)
    versions: dict[str, str] = {}
    for path, raw_version in manifest.items():
        version = _string(raw_version, f"version for {path}")
        if VERSION_PATTERN.fullmatch(version) is None:
            message = f"Version for {path} is not X.Y.Z: {version}"
            raise ReleasePlanError(message)
        versions[path] = version
    return versions


def _project_metadata(text: str, path: str) -> tuple[str, str, tuple[str, ...]]:
    document = _toml_object(text, path)
    project = _object_dict(document.get("project"), f"{path} project")
    name = _string(project.get("name"), f"{path} project.name")
    version = _string(project.get("version"), f"{path} project.version")
    raw_dependencies = project.get("dependencies", [])
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(item, str) for item in raw_dependencies
    ):
        message = f"{path} project.dependencies must contain only strings"
        raise ReleasePlanError(message)
    return name, version, tuple(cast("list[str]", raw_dependencies))


def _tag_for(package: PackageConfig, version: str) -> str:
    return f"{package.component}-v{version}"


def _changelog_body(changelog: str, version: str, path: str) -> str:
    lines = changelog.splitlines()
    heading = re.compile(
        rf"^## \[?{re.escape(version)}\]?(?:\([^)]*\))?(?: \([^)]*\))?$"
    )
    start: int | None = None
    for index, line in enumerate(lines):
        if heading.fullmatch(line):
            start = index + 1
            break
    if start is None:
        message = f"{path} has no release entry for {version}"
        raise ReleasePlanError(message)
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    body = "\n".join(lines[start:end]).strip()
    if not body:
        message = f"{path} has an empty release entry for {version}"
        raise ReleasePlanError(message)
    return body


def _validate_package_state(
    packages: tuple[PackageConfig, ...],
    versions: dict[str, str],
    read_text: Callable[[str], str],
) -> None:
    if set(versions) != {package.path for package in packages}:
        message = "Release Please package and manifest paths do not match"
        raise ReleasePlanError(message)
    for package in packages:
        text = read_text(package.pyproject_path)
        name, version, dependencies = _project_metadata(text, package.pyproject_path)
        if name != package.package_name:
            message = (
                f"Configured package name {package.package_name} does not match {name}"
            )
            raise ReleasePlanError(message)
        if version != versions[package.path]:
            message = (
                f"Manifest version {versions[package.path]} does not match "
                f"{package.pyproject_path} version {version}"
            )
            raise ReleasePlanError(message)
        for dependency in INTERNAL_DEPENDENCIES.get(package.path, ()):
            if not any(item.startswith(f"{dependency}>=") for item in dependencies):
                message = (
                    f"{package.package_name} must declare a minimum version for "
                    f"{dependency}"
                )
                raise ReleasePlanError(message)


def validate_repository(root: Path = ROOT) -> None:
    """Validate current release configuration and package metadata."""
    packages = load_package_configs((root / CONFIG_PATH).read_text(encoding="utf-8"))
    versions = load_versions((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    _validate_package_state(
        packages,
        versions,
        lambda path: (root / path).read_text(encoding="utf-8"),
    )


def _write_github_outputs(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as output:
        for name, value in values.items():
            output.write(f"{name}={value}\n")


def plan_release(
    base_ref: str,
    head_ref: str,
    output_directory: Path,
    *,
    root: Path = ROOT,
    github_output: Path | None = None,
) -> ReleasePlan:
    """Plan an aggregate release by comparing two repository revisions."""
    for name, ref in (("base", base_ref), ("head", head_ref)):
        if COMMIT_PATTERN.fullmatch(ref) is None:
            message = f"{name} ref must be a full lowercase commit SHA"
            raise ReleasePlanError(message)
    config_text = _repository_text(root, head_ref, CONFIG_PATH)
    packages = load_package_configs(config_text)
    before = load_versions(_repository_text(root, base_ref, MANIFEST_PATH))
    after = load_versions(_repository_text(root, head_ref, MANIFEST_PATH))
    _validate_package_state(
        packages,
        after,
        lambda path: _repository_text(root, head_ref, path),
    )
    if set(before) != set(after):
        message = "Adding or removing release packages requires a separate migration"
        raise ReleasePlanError(message)
    changed_paths = {path for path, version in after.items() if before[path] != version}
    if not changed_paths:
        message = "The merged release PR does not change any package versions"
        raise ReleasePlanError(message)
    if PRODUCT_PATH not in changed_paths:
        message = "Every component release must include a Lyra product version bump"
        raise ReleasePlanError(message)

    components = tuple(
        ComponentRelease(
            name=package.package_name,
            path=package.path,
            version=after[package.path],
            tag=_tag_for(package, after[package.path]),
            changed=package.path in changed_paths,
        )
        for package in packages
    )
    for package, component in zip(packages, components, strict=True):
        if component.changed:
            _changelog_body(
                _repository_text(root, head_ref, package.repository_changelog_path),
                component.version,
                package.repository_changelog_path,
            )

    product = components[0]
    product_changes = _changelog_body(
        _repository_text(root, head_ref, packages[0].repository_changelog_path),
        product.version,
        packages[0].repository_changelog_path,
    )
    commit = _repository_sha(root, head_ref)
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = output_directory / "release-manifest.json"
    manifest = {
        "schema_version": 1,
        "product": {
            "name": PRODUCT_COMPONENT,
            "version": product.version,
            "tag": product.tag,
        },
        "commit": commit,
        "components": [asdict(component) for component in components],
    }
    manifest_path.write_text(f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8")

    notes_path = output_directory / "release-notes.md"
    rows = ["| Component | Version |", "| --- | --- |"]
    rows.extend(
        f"| `{component.name}` | `{before[component.path]} -> {component.version}` |"
        if component.changed
        else f"| `{component.name}` | `{component.version}` |"
        for component in components
    )
    notes_path.write_text(
        "\n".join(
            [
                "## Component versions",
                "",
                *rows,
                "",
                "## What's Changed",
                "",
                product_changes,
                "",
            ]
        ),
        encoding="utf-8",
    )

    changed_components = tuple(
        component for component in components[1:] if component.changed
    )
    plan = ReleasePlan(
        product_version=product.version,
        product_tag=product.tag,
        changed_components=changed_components,
        notes_path=notes_path,
        manifest_path=manifest_path,
    )
    if github_output is not None:
        _write_github_outputs(
            github_output,
            {
                "product_version": plan.product_version,
                "product_tag": plan.product_tag,
                "changed_components": json.dumps(
                    [asdict(component) for component in changed_components],
                    separators=(",", ":"),
                ),
                "release_notes": str(notes_path),
                "release_manifest": str(manifest_path),
                "release_commit": commit,
            },
        )
    return plan


def build_parser() -> argparse.ArgumentParser:
    """Build the release tooling command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate current release metadata")
    plan = subparsers.add_parser("plan", help="plan a release from two Git refs")
    plan.add_argument("--base-ref", required=True)
    plan.add_argument("--head-ref", required=True)
    plan.add_argument("--output-dir", type=Path, required=True)
    plan.add_argument("--github-output", type=Path)
    return parser


def main(arguments: list[str] | None = None) -> int:
    """Run release validation or planning from the command line."""
    options = build_parser().parse_args(arguments)
    try:
        if options.command == "validate":
            validate_repository()
        else:
            plan_release(
                options.base_ref,
                options.head_ref,
                options.output_dir,
                github_output=options.github_output,
            )
    except ReleasePlanError as error:
        sys.stderr.write(f"release error: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
