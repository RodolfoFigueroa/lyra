import json
import subprocess
from pathlib import Path

import pytest

from lyra_app.plugins import (
    MANIFEST_FILENAME,
    PluginSyncError,
    iter_plugin_entries,
    parse_repo_entry,
    sync_plugin_repo,
    sync_plugin_repos,
)
from tests.smoke_plugin_helpers import directory_uri


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


def test_parse_repo_entry_accepts_dir_uri_for_directory(tmp_path: Path) -> None:
    source = tmp_path / "mock-plugin"
    entry = parse_repo_entry(f"dir://{source}")

    assert entry.source_kind == "directory"
    assert entry.clone_url == f"dir://{source.resolve().as_posix()}"
    assert entry.owner == "dir"
    assert entry.repo == "mock-plugin"
    assert entry.ref is None
    assert entry.source_path == source.resolve()
    assert entry.display_name == f"dir:{source.resolve()}"
    assert entry.target_name.startswith("dir__mock-plugin__")
    assert len(entry.target_name.removeprefix("dir__mock-plugin__")) == 12


def test_parse_repo_entry_accepts_localhost_dir_uri(tmp_path: Path) -> None:
    source = tmp_path / "mock-plugin"
    entry = parse_repo_entry(f"dir://localhost{source}")

    assert entry.source_kind == "directory"
    assert entry.clone_url == f"dir://{source.resolve().as_posix()}"
    assert entry.source_path == source.resolve()


def test_parse_repo_entry_percent_encodes_directory_uri(tmp_path: Path) -> None:
    source = tmp_path / "mock plugin"
    entry = parse_repo_entry(f"dir://{source}")

    assert entry.source_kind == "directory"
    assert entry.clone_url == directory_uri(source)
    assert "%20" in entry.clone_url
    assert " " not in entry.clone_url


def test_parse_repo_entry_directory_target_does_not_collide_with_local_repo(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mock-plugin"

    local_entry = parse_repo_entry(source.as_uri())
    directory_entry = parse_repo_entry(f"dir://{source}")

    assert local_entry.target_name.startswith("local__mock-plugin__")
    assert directory_entry.target_name.startswith("dir__mock-plugin__")
    assert directory_entry.target_name != local_entry.target_name


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


@pytest.mark.parametrize(
    "raw",
    [
        "dir:mock-plugin",
        "dir://example.com/plugin",
        "dir:///tmp/mock-plugin?ignored=1",
        "dir:///tmp/mock-plugin#fragment",
        "dir:///tmp/mock-plugin@main",
    ],
)
def test_parse_repo_entry_rejects_malformed_directory_entries(raw: str) -> None:
    with pytest.raises(ValueError, match="plugin"):
        parse_repo_entry(raw)


def test_parse_repo_entry_rejects_raw_directory_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="plugin repo"):
        parse_repo_entry(str(tmp_path / "mock-plugin"))


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


def test_sync_plugin_repos_copies_directory_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "initial")
    target_dir = tmp_path / "targets"

    synced = sync_plugin_repos(target_dir, [f"dir://{source}"])

    assert len(synced) == 1
    repo = synced[0]
    assert repo.changed is True
    assert repo.path == target_dir / repo.entry.target_name
    assert json.loads((repo.path / MANIFEST_FILENAME).read_text(encoding="utf-8")) == {
        "marker": "initial",
    }
    assert (target_dir / f".{repo.entry.target_name}.fingerprint").exists()
    assert not (repo.path / f".{repo.entry.target_name}.fingerprint").exists()


def test_sync_plugin_repos_reports_unchanged_directory_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "initial")
    target_dir = tmp_path / "targets"
    sync_plugin_repos(target_dir, [f"dir://{source}"])

    synced = sync_plugin_repos(target_dir, [f"dir://{source}"])

    assert len(synced) == 1
    assert synced[0].changed is False


def test_sync_plugin_repo_reflects_directory_edits_adds_and_deletes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "initial")
    target_dir = tmp_path / "targets"
    initial = sync_plugin_repo(target_dir, f"dir://{source}")
    runner = source / "runner.py"

    _write_manifest(source, "edited")
    runner.write_text("VALUE = 1\n", encoding="utf-8")
    edited = sync_plugin_repo(target_dir, f"dir://{source}")
    edited_manifest = json.loads(
        (edited.path / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    edited_runner = (edited.path / "runner.py").read_text(encoding="utf-8")

    runner.unlink()
    deleted = sync_plugin_repo(target_dir, f"dir://{source}")

    assert initial.changed is True
    assert edited.changed is True
    assert edited_manifest == {"marker": "edited"}
    assert edited_runner == "VALUE = 1\n"
    assert deleted.changed is True
    assert not (deleted.path / "runner.py").exists()


def test_sync_plugin_repos_ignores_directory_source_artifacts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "initial")
    (source / ".git").mkdir()
    (source / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "runner.cpython-311.pyc").write_bytes(b"cache")
    (source / "mock_plugin.egg-info").mkdir()
    (source / "mock_plugin.egg-info" / "PKG-INFO").write_text(
        "Name: mock-plugin\n",
        encoding="utf-8",
    )
    (source / "runner.pyc").write_bytes(b"cache")
    target_dir = tmp_path / "targets"

    repo = sync_plugin_repo(target_dir, f"dir://{source}")

    assert repo.changed is True
    assert not (repo.path / ".git").exists()
    assert not (repo.path / "__pycache__").exists()
    assert not (repo.path / "mock_plugin.egg-info").exists()
    assert not (repo.path / "runner.pyc").exists()


def test_sync_plugin_repos_ignores_artifact_only_directory_changes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "initial")
    target_dir = tmp_path / "targets"
    sync_plugin_repo(target_dir, f"dir://{source}")

    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "runner.cpython-311.pyc").write_bytes(b"one")
    (source / "mock_plugin.egg-info").mkdir()
    (source / "mock_plugin.egg-info" / "PKG-INFO").write_text(
        "Name: mock-plugin\n",
        encoding="utf-8",
    )
    (source / "runner.pyc").write_bytes(b"cache")

    repo = sync_plugin_repo(target_dir, f"dir://{source}")

    assert repo.changed is False


def test_sync_plugin_repos_preserves_directory_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "initial")
    (source / "target.txt").write_text("target\n", encoding="utf-8")
    (source / "link.txt").symlink_to("target.txt")

    repo = sync_plugin_repo(tmp_path / "targets", f"dir://{source}")

    copied_link = repo.path / "link.txt"
    assert copied_link.is_symlink()
    assert copied_link.readlink() == Path("target.txt")


def test_sync_plugin_repos_skips_missing_directory_source_by_default(
    tmp_path: Path,
) -> None:
    synced = sync_plugin_repos(tmp_path / "targets", [f"dir://{tmp_path / 'missing'}"])

    assert synced == []


def test_sync_plugin_repos_raises_for_missing_directory_source(
    tmp_path: Path,
) -> None:
    with pytest.raises(PluginSyncError, match="does not exist"):
        sync_plugin_repos(
            tmp_path / "targets",
            [f"dir://{tmp_path / 'missing'}"],
            raise_on_error=True,
        )


def test_sync_plugin_repo_raises_for_missing_directory_source(
    tmp_path: Path,
) -> None:
    with pytest.raises(PluginSyncError, match="does not exist"):
        sync_plugin_repo(tmp_path / "targets", f"dir://{tmp_path / 'missing'}")


def test_sync_plugin_repo_raises_for_file_directory_source(tmp_path: Path) -> None:
    source = tmp_path / "plugin.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(PluginSyncError, match="not a directory"):
        sync_plugin_repo(tmp_path / "targets", f"dir://{source}")
