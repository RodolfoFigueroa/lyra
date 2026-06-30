import json
import subprocess
from pathlib import Path

import pytest

from lyra_app.plugins import (
    MANIFEST_FILENAME,
    iter_plugin_entries,
    parse_repo_entry,
    sync_plugin_repos,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
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


@pytest.mark.parametrize(
    ("raw", "clone_url", "owner", "repo", "ref"),
    [
        (
            "owner/plugin-a",
            "https://github.com/owner/plugin-a.git",
            "owner",
            "plugin-a",
            None,
        ),
        (
            "owner/plugin-b@main",
            "https://github.com/owner/plugin-b.git",
            "owner",
            "plugin-b",
            "main",
        ),
        (
            "https://github.com/owner/plugin-c@v0.1.0",
            "https://github.com/owner/plugin-c.git",
            "owner",
            "plugin-c",
            "v0.1.0",
        ),
    ],
)
def test_parse_repo_entry_keeps_github_formats_unchanged(
    raw: str,
    clone_url: str,
    owner: str,
    repo: str,
    ref: str | None,
) -> None:
    entry = parse_repo_entry(raw)

    assert entry.source_kind == "github"
    assert entry.clone_url == clone_url
    assert entry.owner == owner
    assert entry.repo == repo
    assert entry.ref == ref
    assert entry.source_path is None
    assert entry.display_name == f"{owner}/{repo}"
    assert entry.target_name == f"{owner}__{repo}"


def test_parse_repo_entry_accepts_file_uri_for_local_repo(tmp_path: Path) -> None:
    source = tmp_path / "local plugin"
    entry = parse_repo_entry(source.as_uri())

    assert entry.source_kind == "local"
    assert entry.clone_url == source.resolve().as_uri()
    assert entry.owner == "local"
    assert entry.repo == "local plugin"
    assert entry.ref is None
    assert entry.source_path == source.resolve()
    assert entry.display_name == f"local:{source.resolve()}"
    assert entry.target_name.startswith("local__local_plugin__")
    assert len(entry.target_name.removeprefix("local__local_plugin__")) == 12


def test_parse_repo_entry_accepts_localhost_file_uri(tmp_path: Path) -> None:
    source = tmp_path / "local-plugin"
    entry = parse_repo_entry(f"file://localhost{source}")

    assert entry.source_kind == "local"
    assert entry.clone_url == source.resolve().as_uri()
    assert entry.source_path == source.resolve()


@pytest.mark.parametrize(
    "raw",
    [
        "file:relative-plugin",
        "file://example.com/plugin",
        "file:///plugin?ignored=1",
        "file:///plugin#fragment",
        "file:///plugin@main",
        "/plugin",
    ],
)
def test_parse_repo_entry_rejects_malformed_local_entries(raw: str) -> None:
    with pytest.raises(ValueError, match="plugin repo"):
        parse_repo_entry(raw)


def test_iter_plugin_entries_skips_malformed_local_entries() -> None:
    entries = list(iter_plugin_entries(["file:relative-plugin", "owner/repo"]))

    assert [entry.display_name for entry in entries] == ["owner/repo"]


def test_sync_plugin_repos_clones_local_repo(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _init_local_plugin_repo(source)
    target_dir = tmp_path / "targets"

    synced = sync_plugin_repos(target_dir, [source.as_uri()])

    assert len(synced) == 1
    repo = synced[0]
    assert repo.changed is True
    assert repo.path == target_dir / repo.entry.target_name
    assert json.loads((repo.path / MANIFEST_FILENAME).read_text(encoding="utf-8")) == {
        "marker": "initial",
    }


def test_sync_plugin_repos_updates_local_repo_after_commit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _init_local_plugin_repo(source)
    target_dir = tmp_path / "targets"
    sync_plugin_repos(target_dir, [source.as_uri()])

    _write_manifest(source, "committed")
    _commit_all(source, "Update manifest")
    synced = sync_plugin_repos(target_dir, [source.as_uri()])

    assert len(synced) == 1
    repo = synced[0]
    assert repo.changed is True
    assert json.loads((repo.path / MANIFEST_FILENAME).read_text(encoding="utf-8")) == {
        "marker": "committed",
    }


def test_sync_plugin_repos_ignores_uncommitted_local_repo_changes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _init_local_plugin_repo(source)
    target_dir = tmp_path / "targets"
    sync_plugin_repos(target_dir, [source.as_uri()])

    _write_manifest(source, "uncommitted")
    synced = sync_plugin_repos(target_dir, [source.as_uri()])

    assert len(synced) == 1
    repo = synced[0]
    assert repo.changed is False
    assert json.loads((repo.path / MANIFEST_FILENAME).read_text(encoding="utf-8")) == {
        "marker": "initial",
    }
