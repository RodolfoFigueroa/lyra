import json
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- test doubles
from pathlib import Path

import pytest
from lyra.sdk.config import PluginRepoConfig

from lyra_app import registry
from lyra_app.config import clear_config_cache
from lyra_app.plugin_runtime import read_snapshot
from lyra_app.plugins import (
    MANIFEST_FILENAME,
    PluginCaptureError,
    capture_plugin_source,
)
from tests.config_helpers import load_test_config
from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR, directory_uri


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        ["git", "-C", str(repo), *args],  # ruff:ignore[start-process-with-partial-path]
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_manifest(repo: Path, marker: str) -> None:
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps({"marker": marker}),
        encoding="utf-8",
    )


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", MANIFEST_FILENAME)
    _git(repo, "commit", "-m", message)


def _init_local_plugin_repo(repo: Path, marker: str = "initial") -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _write_manifest(repo, marker)
    _commit_all(repo, "Initial manifest")


@pytest.mark.parametrize("scheme", ["file", "dir"])
def test_capture_selects_committed_or_working_files(
    tmp_path: Path, scheme: str
) -> None:
    source = tmp_path / "plugin @dev"
    _init_local_plugin_repo(source)
    revision = _git(source, "rev-parse", "HEAD")
    _write_manifest(source, "uncommitted")
    (source / "new.txt").write_text("working copy")
    config = PluginRepoConfig(id="example", source=f"{scheme}://{source}")
    target = tmp_path / "captured"

    assert capture_plugin_source(config, target) == (
        revision if scheme == "file" else None
    )
    expected = "initial" if scheme == "file" else "uncommitted"
    assert json.loads((target / MANIFEST_FILENAME).read_text()) == {"marker": expected}
    assert (target / "new.txt").exists() == (scheme == "dir")
    assert not (target / ".git").exists()
    assert not list(tmp_path.glob(".*.fingerprint"))
    assert not list(tmp_path.glob(".git-capture-*"))


def test_directory_capture_is_fresh_and_excludes_build_artifacts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "initial")
    ignored = [
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".ty",
        ".venv",
        "build",
        "dist",
        "plugin.egg-info",
    ]
    for name in ignored:
        (source / name).mkdir()
        (source / name / "content").write_text("excluded")
    (source / "runner.pyc").write_bytes(b"cache")
    (source / "old.py").write_text("old")
    repo = PluginRepoConfig(id="directory", source=directory_uri(source))
    initial = tmp_path / "initial"
    capture_plugin_source(repo, initial)
    _write_manifest(source, "updated")
    (source / "old.py").unlink()
    (source / "new.py").write_text("new")
    updated = tmp_path / "updated"
    capture_plugin_source(repo, updated)

    assert {p.name for p in initial.iterdir()} == {MANIFEST_FILENAME, "old.py"}
    assert {p.name for p in updated.iterdir()} == {MANIFEST_FILENAME, "new.py"}
    assert json.loads((initial / MANIFEST_FILENAME).read_text())["marker"] == "initial"
    assert json.loads((updated / MANIFEST_FILENAME).read_text())["marker"] == "updated"


@pytest.mark.parametrize("target_kind", ["directory", "file", "broken_symlink"])
def test_capture_refuses_existing_destinations(
    tmp_path: Path, target_kind: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"
    if target_kind == "directory":
        target.mkdir()
        (target / "sentinel").write_text("keep")
    elif target_kind == "file":
        target.write_text("keep")
    else:
        target.symlink_to("missing")
    with pytest.raises(PluginCaptureError, match="already exists") as error:
        capture_plugin_source(
            PluginRepoConfig(id="existing", source=directory_uri(source)), target
        )
    assert error.value.__cause__ is not None
    if target_kind == "directory":
        assert (target / "sentinel").read_text() == "keep"
    elif target_kind == "file":
        assert target.read_text() == "keep"
    else:
        assert target.is_symlink()


@pytest.mark.parametrize("source_kind", ["missing", "file"])
def test_capture_reports_invalid_local_sources(
    tmp_path: Path, source_kind: str
) -> None:
    source = tmp_path / "source"
    if source_kind == "file":
        source.write_text("not a directory")
    target = tmp_path / "new-parent" / "captured"
    with pytest.raises(PluginCaptureError, match="'invalid'") as error:
        capture_plugin_source(
            PluginRepoConfig(id="invalid", source=directory_uri(source)), target
        )
    assert error.value.__cause__ is not None
    assert not target.parent.exists()


def test_directory_capture_materializes_file_and_directory_symlinks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "value.txt").write_text("captured")
    (source / "file.txt").symlink_to(external / "value.txt")
    (source / "directory").symlink_to(external, target_is_directory=True)
    target = tmp_path / "captured"
    capture_plugin_source(
        PluginRepoConfig(id="links", source=directory_uri(source)), target
    )
    (external / "value.txt").write_text("changed")
    assert (target / "file.txt").read_text() == "captured"
    assert (target / "directory" / "value.txt").read_text() == "captured"
    assert not (target / "file.txt").is_symlink()
    assert not (target / "directory").is_symlink()


def test_broken_symlink_fails_capture(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "broken").symlink_to("missing")
    with pytest.raises(PluginCaptureError, match="'broken'") as error:
        capture_plugin_source(
            PluginRepoConfig(id="broken", source=directory_uri(source)),
            tmp_path / "target",
        )
    assert isinstance(error.value.__cause__, OSError)


def test_git_failure_preserves_cause_and_removes_checkout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _init_local_plugin_repo(source)
    repo = PluginRepoConfig(
        id="git-failure", source=source.as_uri(), ref="missing-branch"
    )
    with pytest.raises(PluginCaptureError, match="'git-failure'") as error:
        capture_plugin_source(repo, tmp_path / "target")
    assert isinstance(error.value.__cause__, subprocess.CalledProcessError)
    assert not (tmp_path / "target").exists()
    assert not list(tmp_path.glob(".git-capture-*"))


@pytest.mark.parametrize("revision", ["branch", "tag", "commit", "default"])
def test_configured_git_source_resolves_branch_tag_commit_and_default(
    tmp_path: Path, revision: str
) -> None:
    source = tmp_path / "git-source"
    _init_local_plugin_repo(source)
    expected = _git(source, "rev-parse", "HEAD")
    _git(source, "branch", "selected-branch")
    _git(source, "tag", "selected-tag")
    _write_manifest(source, "newer")
    _commit_all(source, "Newer default branch")
    refs = {"branch": "selected-branch", "tag": "selected-tag", "commit": expected}
    if revision == "default":
        expected = _git(source, "rev-parse", "HEAD")
    config = PluginRepoConfig(id="git", source=source.as_uri(), ref=refs.get(revision))

    target = tmp_path / "captured"
    assert capture_plugin_source(config, target) == expected
    expected_manifest = _git(source, "show", f"{expected}:{MANIFEST_FILENAME}")
    assert (target / MANIFEST_FILENAME).read_text() == expected_manifest
    assert not (target / ".git").exists()


@pytest.mark.parametrize("scheme", ["dir", "file"])
def test_capture_rejects_a_destination_inside_its_source(
    tmp_path: Path,
    scheme: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "initial")
    with pytest.raises(PluginCaptureError, match="must not be inside"):
        capture_plugin_source(
            PluginRepoConfig(id="self", source=f"{scheme}://{source}"),
            source / "capture",
        )
    assert not (source / "capture").exists()


def test_git_branch_is_captured_once_per_api_startup(tmp_path: Path) -> None:
    source = tmp_path / "git-source"
    _init_local_plugin_repo(source)
    shutil.copyfile(SMOKE_PLUGIN_DIR / MANIFEST_FILENAME, source / MANIFEST_FILENAME)
    _commit_all(source, "Plugin manifest")
    _git(source, "branch", "selected")
    config = load_test_config(tmp_path)
    config.plugins.repos = [
        PluginRepoConfig(id="git", source=source.as_uri(), ref="selected")
    ]
    try:
        registry.initialize_catalog()
        initial = read_snapshot(config).sources[0]
        assert initial.resolved_ref == _git(source, "rev-parse", "selected")
        manifest = json.loads((source / MANIFEST_FILENAME).read_text())
        manifest["metrics"][0]["description"] = "Changed after startup"
        (source / MANIFEST_FILENAME).write_text(json.dumps(manifest))
        _commit_all(source, "Change manifest")
        _git(source, "branch", "--force", "selected", "HEAD")
        assert read_snapshot(config).sources[0].resolved_ref == initial.resolved_ref
        assert (
            "Changed after startup"
            not in (initial.path / MANIFEST_FILENAME).read_text()
        )
        registry.initialize_catalog()
        updated = read_snapshot(config).sources[0]
        assert updated.resolved_ref == _git(source, "rev-parse", "HEAD")
        assert updated.resolved_ref != initial.resolved_ref
        assert registry.resolved_source_refs() == {"git": updated.resolved_ref}
    finally:
        registry.reset_catalog()
        clear_config_cache()
