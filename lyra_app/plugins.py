import hashlib
import importlib
import logging
import re
import site
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "lyra.plugin.json"
DEFAULT_CATALOG_DIR = Path("/lyra_data/plugins/catalog")
DEFAULT_INSTALL_DIR = Path("/lyra_data/plugins/runners/default")
RepoSourceKind = Literal["github", "local"]


@dataclass(frozen=True)
class PluginRepoEntry:
    raw: str
    clone_url: str
    owner: str
    repo: str
    ref: str | None
    source_kind: RepoSourceKind = "github"
    source_path: Path | None = None

    @property
    def display_name(self) -> str:
        if self.source_kind == "local" and self.source_path is not None:
            return f"local:{self.source_path}"
        return f"{self.owner}/{self.repo}"

    @property
    def target_name(self) -> str:
        if self.source_kind == "local":
            hash_source = str(self.source_path or self.clone_url)
            path_hash = hashlib.sha256(hash_source.encode()).hexdigest()[:12]
            safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.repo) or "repo"
            return f"local__{safe_repo}__{path_hash}"

        safe_owner = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.owner)
        safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.repo)
        return f"{safe_owner}__{safe_repo}"


@dataclass(frozen=True)
class SyncedPluginRepo:
    entry: PluginRepoEntry
    path: Path
    changed: bool


_REPO_RE = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<owner>[^/@]+)/(?P<repo>[^@]+)"
    r"(?:@(?P<ref>[^@\s]+))?$",
)


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


def get_catalog_dir() -> Path:
    return DEFAULT_CATALOG_DIR


def get_install_dir() -> Path:
    return DEFAULT_INSTALL_DIR


def _run_git(*args: str, cwd: Path | None = None) -> str:
    cmd = ["git"]
    if cwd is not None:
        cmd += ["-C", str(cwd)]
    cmd += list(args)
    return subprocess.run(  # noqa: S603
        cmd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def parse_repo_entry(entry: str) -> PluginRepoEntry:
    raw = entry.strip().rstrip("/")
    if raw.lower().startswith("file:"):
        return _parse_local_repo_entry(raw)

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


def iter_plugin_entries(
    raw_entries: Iterable[str] | None = None,
) -> Iterable[PluginRepoEntry]:
    if raw_entries is None:
        return []

    entries_to_parse = [value.strip() for value in raw_entries if value.strip()]
    if not entries_to_parse:
        return []

    entries: list[PluginRepoEntry] = []
    for entry in entries_to_parse:
        try:
            entries.append(parse_repo_entry(entry))
        except ValueError:
            logger.warning("Skipping malformed plugin entry: %r", entry)
    return entries


def _sync_repo(target: Path, entry: PluginRepoEntry) -> bool:
    if not target.exists():
        cmd = ["clone", "--depth=1"]
        if entry.ref:
            cmd += ["--branch", entry.ref]
        cmd += [entry.clone_url, str(target)]
        logger.info("Cloning plugin repo %s -> %s", entry.clone_url, target)
        _run_git(*cmd)
        return True

    fetch_args = ["fetch", "--depth=1", "origin"]
    if entry.ref:
        fetch_args.append(entry.ref)
    _run_git(*fetch_args, cwd=target)

    local = _run_git("rev-parse", "HEAD", cwd=target)
    remote = _run_git("rev-parse", "FETCH_HEAD", cwd=target)
    if local == remote:
        return False

    _run_git("reset", "--hard", "FETCH_HEAD", cwd=target)
    return True


def sync_plugin_repos(
    target_dir: Path,
    raw_entries: Iterable[str] | None = None,
) -> list[SyncedPluginRepo]:
    entries = list(iter_plugin_entries(raw_entries))
    if not entries:
        return []

    target_dir.mkdir(parents=True, exist_ok=True)
    synced: list[SyncedPluginRepo] = []
    used_targets: set[str] = set()

    for entry in entries:
        if entry.target_name in used_targets:
            logger.warning(
                "Skipping duplicate plugin target %r from %s",
                entry.target_name,
                entry.raw,
            )
            continue
        used_targets.add(entry.target_name)

        target = target_dir / entry.target_name
        try:
            changed = _sync_repo(target, entry)
        except subprocess.CalledProcessError:
            logger.warning(
                "Failed to sync plugin repo %r from %s",
                entry.display_name,
                entry.clone_url,
            )
            if target.exists():
                logger.warning(
                    "Using existing plugin checkout for %r at %s.",
                    entry.display_name,
                    target,
                )
                synced.append(SyncedPluginRepo(entry=entry, path=target, changed=False))
            continue
        synced.append(SyncedPluginRepo(entry=entry, path=target, changed=changed))

    return synced


def sync_catalog_repos() -> list[SyncedPluginRepo]:
    return sync_plugin_repos(get_catalog_dir())


def sync_runner_repos(target_dir: Path | None = None) -> list[SyncedPluginRepo]:
    return sync_plugin_repos(target_dir or get_install_dir())


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
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        logger.warning(
            "Plugin %s failed compatibility check and will be skipped. Reason: %s.",
            plugin_dir.name,
            result.stderr,
        )
        return False
    return True


def install_plugin(plugin_dir: Path) -> None:
    logger.info("Installing plugin %s (editable).", plugin_dir.name)
    subprocess.run(  # noqa: S603
        ["uv", "pip", "install", "--python", sys.executable, "-e", str(plugin_dir)],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    for site_dir in site.getsitepackages():
        site.addsitedir(site_dir)
    importlib.invalidate_caches()


def install_runner_plugins(repos: Iterable[SyncedPluginRepo]) -> list[SyncedPluginRepo]:
    installed: list[SyncedPluginRepo] = []
    for repo in repos:
        if not _check_compatible(repo.path):
            continue
        try:
            install_plugin(repo.path)
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Failed to install plugin %r: %s\n%s",
                repo.entry.display_name,
                exc,
                exc.stderr,
            )
            continue
        installed.append(repo)
    return installed


def format_update_message(
    updated: list[str],
    *,
    catalog_changed: bool,
    catalog_fingerprint: str,
) -> str:
    if updated:
        names = ", ".join(updated)
        prefix = f"Updated {len(updated)} plugin repo(s): {names}."
    else:
        prefix = "No plugin repo changes detected."

    changed = "changed" if catalog_changed else "unchanged"
    return (
        f"{prefix} Catalog {changed} ({catalog_fingerprint}). Workers are restarting."
    )
