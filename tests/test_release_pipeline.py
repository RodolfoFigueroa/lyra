from __future__ import annotations

import json
from importlib.metadata import version
from typing import TYPE_CHECKING

import pytest

from build_scripts import release
from lyra_app.version import APP_VERSION

if TYPE_CHECKING:
    from pathlib import Path

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
PACKAGE_DATA = {
    ".": ("lyra", "lyra-app", ("lyra-sdk>=0.11.0", "lyra-utils>=0.2.0")),
    "packages/lyra_sdk": ("lyra-sdk", "lyra-sdk", ()),
    "packages/lyra_api": ("lyra-api", "lyra-api", ("lyra-sdk>=0.11.0",)),
    "packages/lyra_utils": (
        "lyra-utils",
        "lyra-utils",
        ("lyra-sdk>=0.11.0",),
    ),
    "packages/lyra_tui": ("lyra-tui", "lyra-tui", ("lyra-api>=0.6.0",)),
}
CURRENT_VERSIONS = {
    ".": "0.14.1",
    "packages/lyra_sdk": "0.11.0",
    "packages/lyra_api": "0.6.1",
    "packages/lyra_utils": "0.2.0",
    "packages/lyra_tui": "0.5.0",
}
ROOT = release.ROOT


def _config() -> str:
    return json.dumps(
        {
            "skip-github-release": True,
            "packages": {
                path: {
                    "component": component,
                    "package-name": package,
                    **({"exclude-paths": ["docs/**"]} if path == "." else {}),
                }
                for path, (component, package, _dependencies) in PACKAGE_DATA.items()
            },
        }
    )


def _pyproject(name: str, package_version: str, dependencies: tuple[str, ...]) -> str:
    rendered_dependencies = ", ".join(json.dumps(item) for item in dependencies)
    return (
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{package_version}"\n'
        f"dependencies = [{rendered_dependencies}]\n"
    )


def _changelog(package_version: str) -> str:
    return (
        "# Changelog\n\n"
        f"## [{package_version}](https://example.test/compare) (2026-07-21)\n\n"
        "### Bug Fixes\n\n"
        "* Correct the release pipeline\n"
    )


def _repository_files(after: dict[str, str]) -> dict[tuple[str, str], str]:
    files = {
        (BASE_SHA, release.MANIFEST_PATH): json.dumps(CURRENT_VERSIONS),
        (HEAD_SHA, release.MANIFEST_PATH): json.dumps(after),
        (HEAD_SHA, release.CONFIG_PATH): _config(),
    }
    for path, (_component, name, dependencies) in PACKAGE_DATA.items():
        package = release.PackageConfig(
            path=path,
            component=PACKAGE_DATA[path][0],
            package_name=name,
            changelog_path="CHANGELOG.md",
        )
        files[HEAD_SHA, package.pyproject_path] = _pyproject(
            name, after[path], dependencies
        )
        files[HEAD_SHA, package.repository_changelog_path] = _changelog(after[path])
    return files


def _patch_repository(
    monkeypatch: pytest.MonkeyPatch, files: dict[tuple[str, str], str]
) -> None:
    def read_text(_root: Path, ref: str, path: str) -> str:
        return files[ref, path]

    monkeypatch.setattr(release, "_repository_text", read_text)
    monkeypatch.setattr(release, "_repository_sha", lambda _root, _ref: HEAD_SHA)


def test_current_release_configuration_is_consistent() -> None:
    release.validate_repository()
    assert version("lyra-app") == APP_VERSION
    config = json.loads((ROOT / release.CONFIG_PATH).read_text())
    assert config["group-pull-request-title-pattern"] == (
        "chore: release v${{version}}".format()
    )


def test_plan_release_emits_aggregate_manifest_and_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    after = {**CURRENT_VERSIONS, ".": "0.14.2", "packages/lyra_api": "0.6.2"}
    _patch_repository(monkeypatch, _repository_files(after))
    github_output = tmp_path / "github-output"

    plan = release.plan_release(
        BASE_SHA,
        HEAD_SHA,
        tmp_path / "release",
        github_output=github_output,
    )

    assert plan.product_version == "0.14.2"
    assert plan.product_tag == "lyra-v0.14.2"
    assert [component.name for component in plan.changed_components] == ["lyra-api"]
    manifest = json.loads(plan.manifest_path.read_text())
    assert manifest["commit"] == HEAD_SHA
    assert manifest["product"] == {
        "name": "lyra",
        "version": "0.14.2",
        "tag": "lyra-v0.14.2",
    }
    changed = {
        component["name"]
        for component in manifest["components"]
        if component["changed"]
    }
    assert changed == {"lyra-app", "lyra-api"}
    notes = plan.notes_path.read_text()
    assert notes.startswith("## Component versions\n\n")
    assert "# Lyra v0.14.2" not in notes
    assert "| Component | Version |\n| --- | --- |" in notes
    assert "| `lyra-app` | `0.14.1 -> 0.14.2` |" in notes
    assert "| `lyra-sdk` | `0.11.0` |" in notes
    assert "| `lyra-api` | `0.6.1 -> 0.6.2` |" in notes
    assert "| `lyra-utils` | `0.2.0` |" in notes
    assert "| `lyra-tui` | `0.5.0` |" in notes
    assert "Status" not in notes
    assert "Unchanged" not in notes
    assert "product_tag=lyra-v0.14.2" in github_output.read_text()


def test_plan_release_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    after = {**CURRENT_VERSIONS, ".": "0.15.0", "packages/lyra_sdk": "0.12.0"}
    _patch_repository(monkeypatch, _repository_files(after))

    first = release.plan_release(BASE_SHA, HEAD_SHA, tmp_path / "first")
    second = release.plan_release(BASE_SHA, HEAD_SHA, tmp_path / "second")

    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.notes_path.read_bytes() == second.notes_path.read_bytes()


def test_plan_release_requires_product_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    after = {**CURRENT_VERSIONS, "packages/lyra_tui": "0.5.1"}
    _patch_repository(monkeypatch, _repository_files(after))

    with pytest.raises(
        release.ReleasePlanError,
        match="Every component release must include a Lyra product version bump",
    ):
        release.plan_release(BASE_SHA, HEAD_SHA, tmp_path)


def test_plan_release_requires_changed_component_changelog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    after = {**CURRENT_VERSIONS, ".": "0.14.2", "packages/lyra_utils": "0.2.1"}
    files = _repository_files(after)
    files[HEAD_SHA, "packages/lyra_utils/CHANGELOG.md"] = "# Changelog\n"
    _patch_repository(monkeypatch, files)

    with pytest.raises(
        release.ReleasePlanError, match=r"has no release entry for 0\.2\.1"
    ):
        release.plan_release(BASE_SHA, HEAD_SHA, tmp_path)


def test_release_configuration_rejects_package_exclusions() -> None:
    config = json.loads(_config())
    config["packages"]["."]["exclude-paths"].append("packages/lyra_api/**")

    with pytest.raises(release.ReleasePlanError, match="may exclude only docs"):
        release.load_package_configs(json.dumps(config))


def test_release_configuration_rejects_unsafe_tag_prefixes() -> None:
    config = json.loads(_config())
    config["packages"]["packages/lyra_api"]["component"] = "lyra-api\nother"

    with pytest.raises(release.ReleasePlanError, match="not a safe tag prefix"):
        release.load_package_configs(json.dumps(config))


def test_release_plan_requires_full_commit_shas(tmp_path: Path) -> None:
    with pytest.raises(
        release.ReleasePlanError, match="must be a full lowercase commit SHA"
    ):
        release.plan_release("HEAD^", HEAD_SHA, tmp_path)


def test_release_please_only_manages_the_aggregate_pr() -> None:
    workflow = (ROOT / ".github/workflows/release-please.yml").read_text()

    assert "googleapis/release-please-action@" in workflow
    assert "RELEASE_PLEASE_TOKEN" in workflow
    assert "gh release" not in workflow
    assert "github-builder" not in workflow


def test_publisher_is_gated_and_orders_irreversible_operations() -> None:
    workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()

    assert "github.event.pull_request.merged == true" in workflow
    assert "autorelease: pending" in workflow
    assert "release-please--branches--main" in workflow
    assert "uv build --all-packages" in workflow
    assert "tag-components:" in workflow
    assert "publish-app-image:" in workflow
    assert "publish-product-release:" in workflow
    assert "gh release create" in workflow
    assert workflow.index("validate:") < workflow.index("tag-components:")
    assert workflow.index("tag-components:") < workflow.index(
        "publish-product-release:"
    )
