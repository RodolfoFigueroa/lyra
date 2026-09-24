"""Capture plugin sources at startup and install private worker copies."""

import importlib
import logging
import shutil
import site
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- invokes Git/uv
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lyra.sdk.config import PluginRepoConfig

from lyra_app.plugin_runtime import copy_source

logger = logging.getLogger(__name__)
MANIFEST_FILENAME = "lyra.plugin.json"
_DIRECTORY_IGNORE_PATTERNS = (
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".ty",
    ".venv",
    "build",
    "dist",
    "*.egg-info",
    "*.pyc",
)


class PluginCaptureError(RuntimeError):
    """A configured plugin source could not be captured."""


@dataclass(frozen=True)
class PluginLocation:
    """Identify a captured repository or its private worker copy."""

    repo_id: str
    path: Path


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


def capture_plugin_source(repo: PluginRepoConfig, target: Path) -> str | None:
    """Capture a source into a new destination owned by startup staging.

    The caller owns staging cleanup if capture or subsequent validation fails.

    Returns:
        The exact Git commit, or ``None`` for a directory source.

    Raises:
        PluginCaptureError: If the destination is unsafe or capture fails.
    """
    try:
        return _capture_source(repo, target)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        msg = f"Could not capture plugin source {repo.id!r}: {exc}"
        raise PluginCaptureError(msg) from exc


def _capture_source(repo: PluginRepoConfig, target: Path) -> str | None:
    source = repo.parsed_source
    if target.exists() or target.is_symlink():
        msg = "Capture destination already exists."
        raise ValueError(msg)
    local_path = source.path
    if local_path is not None:
        if not local_path.exists():
            msg = "Local plugin source does not exist."
            raise ValueError(msg)
        if not local_path.is_dir():
            msg = "Local plugin source is not a directory."
            raise ValueError(msg)
        if target.resolve().is_relative_to(local_path.resolve()):
            msg = "Plugin capture directory must not be inside the source directory."
            raise ValueError(msg)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.kind == "directory" and local_path is not None:
        shutil.copytree(
            local_path,
            target,
            ignore=shutil.ignore_patterns(*_DIRECTORY_IGNORE_PATTERNS),
        )
        return None
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".git-capture-") as root:
        checkout = Path(root)
        _run_git("init", str(checkout))
        _run_git("remote", "add", "origin", source.git_url, cwd=checkout)
        _run_git("fetch", "--depth=1", "origin", repo.ref or "HEAD", cwd=checkout)
        revision = _run_git("rev-parse", "FETCH_HEAD", cwd=checkout)
        _run_git("checkout", "--force", "--detach", revision, cwd=checkout)
        copy_source(checkout, target)
    return revision


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


def install_runner_plugins(repos: Iterable[PluginLocation]) -> None:
    """Compatibility-check and install each captured runner plugin.

    Raises:
        RuntimeError: If a required plugin is incompatible.
    """
    for repo in repos:
        if not _check_compatible(repo.path):
            msg = f"Required plugin {repo.repo_id!r} is incompatible."
            raise RuntimeError(msg)
        install_plugin(repo.path)
