import importlib
import logging
import os
import re
import site
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGINS_TARGET_DIR = Path("/lyra_plugins")

_PLUGINS_LOADED = False

# Matches:
#   owner/repo
#   owner/repo@ref
#   https://github.com/owner/repo
#   https://github.com/owner/repo@ref
_REPO_RE = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<owner>[^/@]+)/(?P<repo>[^@]+)"
    r"(?:@(?P<ref>[^@\s]+))?$",
)


def _run_git(*args: str, cwd: Path | None = None) -> str:
    """Run a git subcommand and return its stdout.

    Args:
        *args: Git subcommand and arguments (e.g. ``"fetch"``, ``"--depth=1"``).
        cwd: Working directory passed to ``git -C``. If ``None``, no ``-C``
            flag is added.

    Returns:
        str: Stripped stdout of the git process.

    Raises:
        subprocess.CalledProcessError: If git exits with a non-zero status.
    """
    cmd = ["git"]
    if cwd is not None:
        cmd += ["-C", str(cwd)]
    cmd += list(args)
    return subprocess.run(  # noqa: S603
        cmd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _parse_repo_entry(entry: str) -> tuple[str, str, str | None]:
    """Parse a repo entry string into its components.

    Accepts the following formats: ``owner/repo``, ``owner/repo@ref``,
    ``https://github.com/owner/repo``, or
    ``https://github.com/owner/repo@ref``.

    Args:
        entry (str): Raw entry string from ``LYRA_PLUGIN_REPOS``.

    Returns:
        tuple[str, str, str | None]: A ``(clone_url, repo_name, ref)`` tuple
        where ``clone_url`` is the full HTTPS clone URL, ``repo_name`` is the
        repository name, and ``ref`` is the branch/tag/commit or ``None`` if
        not specified.

    Raises:
        ValueError: If the entry does not match the expected format.
    """
    entry = entry.strip().rstrip("/")
    m = _REPO_RE.match(entry)
    if not m:
        err = f"Cannot parse plugin repo entry: {entry!r}"
        raise ValueError(err)
    owner = m.group("owner")
    repo = m.group("repo")
    ref = m.group("ref")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return clone_url, repo, ref


def _iter_plugin_entries() -> Iterator[tuple[str, str, str | None, Path]]:
    """Yield plugin entries parsed from the ``LYRA_PLUGIN_REPOS`` environment variable.

    Reads and splits the comma-separated list. Malformed entries are logged as
    warnings and skipped. Creates ``PLUGINS_TARGET_DIR`` as a side effect
    before yielding if there is at least one valid entry.

    Yields:
        tuple[str, str, str | None, Path]: A ``(clone_url, repo_name, ref,
        target)`` tuple for each valid entry, where ``target`` is the resolved
        local path under ``PLUGINS_TARGET_DIR``.
    """
    raw = os.environ.get("LYRA_PLUGIN_REPOS", "").strip()
    if not raw:
        return

    PLUGINS_TARGET_DIR.mkdir(parents=True, exist_ok=True)

    for entry in (e.strip() for e in raw.split(",") if e.strip()):
        try:
            clone_url, repo_name, ref = _parse_repo_entry(entry)
        except ValueError:
            logger.warning("Skipping malformed plugin entry: %r", entry)
            continue
        yield clone_url, repo_name, ref, PLUGINS_TARGET_DIR / repo_name


def _sync_repo(target: Path, clone_url: str, ref: str | None) -> bool:
    """Ensure *target* is at the latest commit of *ref* (or the default branch).

    If *target* does not exist, the repository is cloned. Otherwise, a fetch
    is performed and the local checkout is compared to ``FETCH_HEAD``; the
    working tree is updated only when they differ.

    Args:
        target (Path): Local directory where the repository should be checked
            out.
        clone_url (str): HTTPS URL to clone from if the directory is absent.
        ref (str | None): Branch, tag, or commit to track. If ``None``, the
            remote's default branch is used.

    Returns:
        bool: ``True`` if the repository was cloned or updated, ``False`` if
        it was already at the latest commit.

    Raises:
        subprocess.CalledProcessError: If any git operation fails.
    """
    if not target.exists():
        cmd = ["clone", "--depth=1"]
        if ref:
            cmd += ["--branch", ref]
        cmd += [clone_url, str(target)]
        logger.info("Cloning plugin repo %s → %s", clone_url, target)
        _run_git(*cmd)
        return True

    fetch_args = ["fetch", "--depth=1", "origin"]
    if ref:
        fetch_args.append(ref)
    _run_git(*fetch_args, cwd=target)

    local = _run_git("rev-parse", "HEAD", cwd=target)
    remote = _run_git("rev-parse", "FETCH_HEAD", cwd=target)

    if local == remote:
        return False

    if ref:
        _run_git("checkout", ref, cwd=target)
    else:
        _run_git("reset", "--hard", "FETCH_HEAD", cwd=target)
    return True


_READY_SENTINEL = ".lyra_ready"


def _mark_ready(target: Path) -> None:
    """Write the ready sentinel file to *target* after a successful install."""
    (target / _READY_SENTINEL).touch()


def _is_ready(target: Path) -> bool:
    """Return whether *target* has been fully set up by the API server."""
    return (target / _READY_SENTINEL).exists()


_WORKER_READY_TIMEOUT = 120.0
_WORKER_READY_POLL = 2.0


def _wait_for_ready(target: Path, repo_name: str) -> bool:
    """Block until *target* has the ready sentinel or the timeout expires.

    Polls every `_WORKER_READY_POLL` seconds. Intended for use in worker mode
    to wait for the API server to finish cloning and installing a plugin before
    this process tries to install it from the shared volume.

    Args:
        target (Path): The plugin directory to wait for.
        repo_name (str): The repository name, used in log messages.

    Returns:
        bool: ``True`` if the sentinel appeared within the timeout window,
        ``False`` if the timeout was exceeded.
    """
    deadline = time.monotonic() + _WORKER_READY_TIMEOUT
    while time.monotonic() < deadline:
        if _is_ready(target):
            return True
        logger.info(
            "Plugin %r: waiting for API server to finish setup (%.0f s remaining)…",
            repo_name,
            deadline - time.monotonic(),
        )
        time.sleep(_WORKER_READY_POLL)
    logger.warning(
        "Plugin %r: timed out after %.0f s waiting for ready sentinel. Skipping.",
        repo_name,
        _WORKER_READY_TIMEOUT,
    )
    return False


def _check_compatible(plugin_dir: Path) -> bool:
    """Check whether installing a plugin would conflict with the current environment.

    Runs ``uv pip install --dry-run`` against the plugin directory and logs a
    warning if the check fails.

    Args:
        plugin_dir (Path): Path to the plugin's root directory.

    Returns:
        bool: ``True`` if the plugin's dependencies are compatible, ``False``
        otherwise.
    """
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


def _install(plugin_dir: Path) -> None:
    """Install a plugin into the current Python environment in editable mode.

    Runs ``uv pip install -e`` and then manually re-processes ``.pth`` files
    so the installed source root is importable within the running process
    without restarting it.

    Args:
        plugin_dir (Path): Path to the plugin's root directory.

    Raises:
        subprocess.CalledProcessError: If ``uv pip install`` fails.
    """
    logger.info("Installing plugin %s (editable).", plugin_dir.name)
    subprocess.run(  # noqa: S603
        ["uv", "pip", "install", "--python", sys.executable, "-e", str(plugin_dir)],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    # uv writes a .pth file into site-packages so the editable source root is on
    # sys.path. Python only processes .pth files at startup, so we must do it
    # manually here for the install to be importable within the running process.
    for site_dir in site.getsitepackages():
        site.addsitedir(site_dir)
    importlib.invalidate_caches()


def load_plugins() -> None:
    """Clone, validate, and install all repos listed in ``LYRA_PLUGIN_REPOS``.

    This function is idempotent: it exits immediately on subsequent calls
    within the same process.

    When the environment variable `LYRA_PLUGIN_MODE=worker` is set, git
    operations and the dry-run compatibility check are skipped. Only
    `uv pip install -e` is executed, which is required because each container
    has its own ephemeral Python environment. If a plugin directory has not
    yet been fully set up by the API server, the worker polls for the
    `.lyra_ready` sentinel file for up to `_WORKER_READY_TIMEOUT` seconds
    before giving up and skipping that plugin.
    """
    global _PLUGINS_LOADED  # noqa: PLW0603
    if _PLUGINS_LOADED:
        return
    _PLUGINS_LOADED = True

    worker_mode = os.environ.get("LYRA_PLUGIN_MODE", "").strip().lower() == "worker"

    for clone_url, repo_name, ref, target in _iter_plugin_entries():
        if worker_mode:
            if not _wait_for_ready(target, repo_name):
                continue
        else:
            try:
                _sync_repo(target, clone_url, ref)
            except subprocess.CalledProcessError:
                logger.warning(
                    "Failed to clone/update plugin %r from %s", repo_name, clone_url
                )
                continue

            if not _check_compatible(target):
                continue

        try:
            _install(target)
            logger.info("Plugin %r loaded successfully.", repo_name)
            _mark_ready(target)
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Failed to install plugin %r: %s\n%s", repo_name, exc, exc.stderr
            )


def reload_plugins() -> list[str]:
    """Re-run plugin loading, skipping repos that have not changed.

    Unlike `load_plugins`, this function is not idempotent — it always
    re-checks remote HEADs and reinstalls any repos that have changed. The
    `_PLUGINS_LOADED` guard is reset before the loop so that `load_plugins`
    becomes a no-op on the next call after all workers have been restarted.

    Returns:
        list[str]: Names of repos that were successfully updated and
        reinstalled.
    """
    global _PLUGINS_LOADED  # noqa: PLW0603
    _PLUGINS_LOADED = False

    updated: list[str] = []

    for clone_url, repo_name, ref, target in _iter_plugin_entries():
        try:
            changed = _sync_repo(target, clone_url, ref)
        except Exception:
            logger.warning("Failed to sync plugin %r from %s.", repo_name, clone_url)
            continue

        if not changed:
            logger.info("Plugin %r is up to date; skipping.", repo_name)
            continue

        logger.info("Plugin %r has changes. Updating from %s", repo_name, clone_url)

        if not _check_compatible(target):
            logger.warning(
                "Plugin %r is incompatible with the current environment"
                " and will be skipped.",
                repo_name,
            )
            continue

        try:
            _install(target)
            logger.info("Plugin %r updated successfully.", repo_name)
            _mark_ready(target)
            updated.append(repo_name)
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Failed to install plugin %r: %s\n%s", repo_name, exc, exc.stderr
            )

    _PLUGINS_LOADED = True
    return updated


def format_update_message(updated: list[str]) -> str:
    """Build a human-readable summary of a plugin update run.

    Args:
        updated (list[str]): Names of plugin repos that were updated.

    Returns:
        str: A message describing how many plugins were updated, or indicating
        that no changes were detected.
    """
    if updated:
        names = ", ".join(updated)
        return f"Updated {len(updated)} plugin(s): {names}. Workers are restarting."
    return (
        "No plugin changes detected. "
        "Workers are restarting to apply any environment changes."
    )
