"""Plugin discovery, loading, validation, and registration."""

import fnmatch
import hashlib
import importlib
import logging
import os
import re
import shutil
import site
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- invokes Git/uv
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urlparse

from lyra.sdk.config import PluginRepoConfig

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "lyra.plugin.json"
RepoSourceKind = Literal["github", "local", "directory"]
_DIRECTORY_IGNORE_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".ty",
        ".venv",
        "build",
        "dist",
    }
)
_DIRECTORY_IGNORE_PATTERNS = ("*.egg-info", "*.pyc")


class PluginSyncError(RuntimeError):
    """Raised when a non-git plugin source cannot be synced."""


@dataclass(frozen=True)
class PluginRepoEntry:
    """Describe a normalized GitHub, local Git, or directory plugin source."""

    raw: str
    clone_url: str
    owner: str
    repo: str
    ref: str | None
    source_kind: RepoSourceKind = "github"
    source_path: Path | None = None

    @property
    def display_name(self) -> str:
        """The human-readable source name used in logs and API responses."""
        if self.source_kind == "local" and self.source_path is not None:
            return f"local:{self.source_path}"
        if self.source_kind == "directory" and self.source_path is not None:
            return f"dir:{self.source_path}"
        return f"{self.owner}/{self.repo}"

    @property
    def target_name(self) -> str:
        """The collision-resistant directory name for the managed snapshot."""
        if self.source_kind in {"directory", "local"}:
            hash_source = str(self.source_path or self.clone_url)
            path_hash = hashlib.sha256(hash_source.encode()).hexdigest()[:12]
            safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.repo) or "repo"
            prefix = "dir" if self.source_kind == "directory" else "local"
            return f"{prefix}__{safe_repo}__{path_hash}"

        safe_owner = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.owner)
        safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.repo)
        return f"{safe_owner}__{safe_repo}"


@dataclass(frozen=True)
class SyncedPluginRepo:
    """Pair a normalized source with its managed path and change status."""

    entry: PluginRepoEntry
    path: Path
    changed: bool


_REPO_RE = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<owner>[^/@]+)/(?P<repo>[^@]+)"
    r"(?:@(?P<ref>[^@\s]+))?$",
)


def _directory_uri_from_path(path: Path) -> str:
    return f"dir://{quote(path.as_posix(), safe='/')}"


def _parse_local_repo_entry(raw: str) -> PluginRepoEntry:
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "file":
        msg = f"Cannot parse plugin repo entry: {raw!r}"
        raise ValueError(msg)
    if parsed.netloc not in {"", "localhost"}:
        msg = f"Local plugin repo file URI must use an empty or localhost host: {raw!r}"
        raise ValueError(msg)
    if parsed.params or parsed.query or parsed.fragment:
        msg = (
            "Local plugin repo file URI cannot include params, query, or "
            f"fragment: {raw!r}"
        )
        raise ValueError(msg)

    raw_path = unquote(parsed.path)
    if not raw_path or "@" in raw_path:
        msg = f"Local plugin repo file URI cannot include refs: {raw!r}"
        raise ValueError(msg)

    source_path = Path(raw_path)
    if not source_path.is_absolute():
        msg = f"Local plugin repo file URI must use an absolute path: {raw!r}"
        raise ValueError(msg)

    source_path = source_path.resolve(strict=False)
    repo = source_path.name or "repo"
    return PluginRepoEntry(
        raw=raw,
        clone_url=source_path.as_uri(),
        owner="local",
        repo=repo,
        ref=None,
        source_kind="local",
        source_path=source_path,
    )


def _parse_directory_entry(raw: str) -> PluginRepoEntry:
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "dir":
        msg = f"Cannot parse plugin repo entry: {raw!r}"
        raise ValueError(msg)
    if parsed.netloc not in {"", "localhost"}:
        msg = f"Directory plugin URI must use an empty or localhost host: {raw!r}"
        raise ValueError(msg)
    if parsed.params or parsed.query or parsed.fragment:
        msg = f"Directory plugin URI cannot include params, query, or fragment: {raw!r}"
        raise ValueError(msg)

    raw_path = unquote(parsed.path)
    if not raw_path or "@" in raw_path:
        msg = f"Directory plugin URI cannot include refs: {raw!r}"
        raise ValueError(msg)

    source_path = Path(raw_path)
    if not source_path.is_absolute():
        msg = f"Directory plugin URI must use an absolute path: {raw!r}"
        raise ValueError(msg)

    source_path = source_path.resolve(strict=False)
    repo = source_path.name or "plugin"
    source_uri = _directory_uri_from_path(source_path)
    return PluginRepoEntry(
        raw=raw,
        clone_url=source_uri,
        owner="dir",
        repo=repo,
        ref=None,
        source_kind="directory",
        source_path=source_path,
    )


def _run_git(*args: str, cwd: Path | None = None) -> str:
    cmd = ["git"]
    if cwd is not None:
        cmd += ["-C", str(cwd)]
    cmd += list(args)
    return subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        cmd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def parse_repo_entry(entry: str) -> PluginRepoEntry:
    """Parse and normalize a supported plugin source string.

    Returns:
        Repository metadata suitable for synchronization and persistence.

    Raises:
        ValueError: If the source is malformed or uses an unsupported URI form.
    """
    raw = entry.strip().rstrip("/")
    if raw.lower().startswith("file:"):
        return _parse_local_repo_entry(raw)
    if raw.lower().startswith("dir:"):
        return _parse_directory_entry(raw)

    match = _REPO_RE.match(raw)
    if match is None:
        msg = f"Cannot parse plugin repo entry: {entry!r}"
        raise ValueError(msg)

    owner = match.group("owner")
    repo = match.group("repo")
    ref = match.group("ref")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return PluginRepoEntry(
        raw=raw,
        clone_url=clone_url,
        owner=owner,
        repo=repo,
        ref=ref,
    )


def _sync_git_repo(target: Path, entry: PluginRepoEntry) -> bool:
    local = None
    if (target / ".git").exists():
        local = _run_git("rev-parse", "HEAD", cwd=target)
        _run_git("remote", "set-url", "origin", entry.clone_url, cwd=target)
    else:
        target.mkdir(parents=True, exist_ok=True)
        _run_git("init", str(target))
        _run_git("remote", "add", "origin", entry.clone_url, cwd=target)
    _run_git("fetch", "--depth=1", "origin", entry.ref or "HEAD", cwd=target)
    remote = _run_git("rev-parse", "FETCH_HEAD", cwd=target)
    _run_git("checkout", "--force", "--detach", remote, cwd=target)
    return local != remote


def prepare_configured_repo(target: Path, repo: PluginRepoConfig) -> SyncedPluginRepo:
    """Capture a configured source in an API-owned startup directory.

    Returns:
        The synchronized source and its path.

    Raises:
        PluginSyncError: If Git cannot fetch the configured revision.
    """
    entry = replace(parse_repo_entry(repo.source), ref=repo.ref)
    try:
        changed = _sync_plugin_source(target, entry)
    except subprocess.CalledProcessError as exc:
        msg = f"Could not capture plugin source {repo.id!r}."
        raise PluginSyncError(msg) from exc
    return SyncedPluginRepo(entry=entry, path=target, changed=changed)


def resolved_git_ref(path: Path) -> str:
    """Read the exact commit captured in a prepared Git source.

    Returns:
        The checkout's commit ID.
    """
    return _run_git("rev-parse", "HEAD", cwd=path)


def _directory_name_ignored(name: str) -> bool:
    return name in _DIRECTORY_IGNORE_NAMES or any(
        fnmatch.fnmatch(name, pattern) for pattern in _DIRECTORY_IGNORE_PATTERNS
    )


def _directory_path_ignored(relative_path: Path) -> bool:
    return any(_directory_name_ignored(part) for part in relative_path.parts)


def _directory_copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if _directory_name_ignored(name)}


def _hash_file(path: Path) -> str:
    file_hash = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            file_hash.update(chunk)
    return file_hash.hexdigest()


def _iter_directory_snapshot_paths(source: Path) -> list[Path]:
    paths: list[Path] = []
    try:
        for root, dir_names, file_names in os.walk(source, followlinks=False):
            dir_names[:] = sorted(
                name for name in dir_names if not _directory_name_ignored(name)
            )
            root_path = Path(root)
            paths.extend(root_path / name for name in dir_names)
            paths.extend(
                root_path / name
                for name in sorted(file_names)
                if not _directory_name_ignored(name)
            )
    except OSError as exc:
        msg = f"Directory plugin source could not be read: {source}"
        raise PluginSyncError(msg) from exc

    return sorted(paths, key=lambda path: path.relative_to(source).as_posix())


def _directory_fingerprint(source: Path) -> str:
    fingerprint = hashlib.sha256()
    for path in _iter_directory_snapshot_paths(source):
        relative_path = path.relative_to(source)
        if _directory_path_ignored(relative_path):
            continue

        relative_name = relative_path.as_posix()
        try:
            entry_type, entry_value = _directory_entry_fingerprint(path)
        except OSError as exc:
            msg = f"Directory plugin source entry could not be read: {path}"
            raise PluginSyncError(msg) from exc

        fingerprint.update(entry_type.encode())
        fingerprint.update(b"\0")
        fingerprint.update(relative_name.encode())
        fingerprint.update(b"\0")
        fingerprint.update(entry_value.encode())
        fingerprint.update(b"\0")

    return fingerprint.hexdigest()


def _directory_entry_fingerprint(path: Path) -> tuple[str, str]:
    if path.is_symlink():
        return "symlink", path.readlink().as_posix()
    if path.is_dir():
        return "directory", ""
    if path.is_file():
        return "file", _hash_file(path)
    msg = f"Directory plugin source contains unsupported entry: {path}"
    raise PluginSyncError(msg)


def _remove_managed_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def _sync_directory_source(target: Path, entry: PluginRepoEntry) -> bool:
    if entry.source_path is None:
        msg = f"Directory plugin source could not be resolved: {entry.raw!r}"
        raise PluginSyncError(msg)

    source = entry.source_path
    if not source.exists():
        msg = f"Directory plugin source does not exist: {source}"
        raise PluginSyncError(msg)
    if not source.is_dir():
        msg = f"Directory plugin source is not a directory: {source}"
        raise PluginSyncError(msg)

    if target.resolve().is_relative_to(source.resolve()):
        msg = "Plugin capture directory must not be inside the source directory."
        raise PluginSyncError(msg)

    fingerprint = _directory_fingerprint(source)
    fingerprint_path = target.parent / f".{target.name}.fingerprint"
    if target.exists() and fingerprint_path.exists():
        try:
            if fingerprint_path.read_text(encoding="utf-8") == fingerprint:
                return False
        except OSError as exc:
            msg = f"Directory plugin fingerprint could not be read: {fingerprint_path}"
            raise PluginSyncError(msg) from exc

    logger.info("Copying plugin directory %s -> %s", source, target)
    with tempfile.TemporaryDirectory(
        dir=target.parent,
        prefix=f".{target.name}.",
    ) as temp_root:
        temp_target = Path(temp_root) / target.name
        try:
            shutil.copytree(
                source,
                temp_target,
                ignore=_directory_copy_ignore,
                symlinks=True,
            )
            _remove_managed_path(target)
            temp_target.replace(target)
            fingerprint_path.write_text(fingerprint, encoding="utf-8")
        except OSError as exc:
            msg = f"Directory plugin source could not be copied: {source}"
            raise PluginSyncError(msg) from exc

    return True


def _sync_plugin_source(target: Path, entry: PluginRepoEntry) -> bool:
    if entry.source_kind == "directory":
        return _sync_directory_source(target, entry)
    return _sync_git_repo(target, entry)


def sync_plugin_repo(target_dir: Path, raw_entry: str) -> SyncedPluginRepo:
    """Synchronize one plugin source into its managed snapshot.

    Returns:
        The normalized source, snapshot path, and whether its content changed.
    """
    entry = parse_repo_entry(raw_entry)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / entry.target_name
    changed = _sync_plugin_source(target, entry)
    return SyncedPluginRepo(entry=entry, path=target, changed=changed)


def _check_compatible(plugin_dir: Path) -> bool:
    cmd = [
        "uv",
        "pip",
        "install",
        "--python",
        sys.executable,
        "--dry-run",
        str(plugin_dir),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # ruff:ignore[subprocess-without-shell-equals-true]
    if result.returncode != 0:
        logger.warning(
            "Plugin %s failed compatibility check. Reason: %s.",
            plugin_dir.name,
            result.stderr,
        )
        return False
    return True


def install_plugin(plugin_dir: Path) -> None:
    """Install one compatible runner plugin editably into the current environment."""
    logger.info("Installing plugin %s (editable).", plugin_dir.name)
    subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        ["uv", "pip", "install", "--python", sys.executable, "-e", str(plugin_dir)],  # ruff:ignore[start-process-with-partial-path]
        check=True,
        capture_output=True,
        text=True,
    )
    for site_dir in site.getsitepackages():
        site.addsitedir(site_dir)
    importlib.invalidate_caches()


def install_runner_plugins(repos: Iterable[SyncedPluginRepo]) -> list[SyncedPluginRepo]:
    """Compatibility-check and install each synchronized runner plugin.

    Returns:
        Repositories whose compatibility check and installation both succeeded.

    Raises:
        RuntimeError: If a required plugin is incompatible.
    """
    installed: list[SyncedPluginRepo] = []
    for repo in repos:
        if not _check_compatible(repo.path):
            msg = f"Required plugin {repo.entry.display_name!r} is incompatible."
            raise RuntimeError(msg)
        install_plugin(repo.path)
        installed.append(repo)
    return installed
