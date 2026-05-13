import logging
import os
import re
import subprocess
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


def _parse_repo_entry(entry: str) -> tuple[str, str, str | None]:
    """Parse a repo entry into (clone_url, repo_name, ref | None)."""
    entry = entry.strip()
    m = _REPO_RE.match(entry)
    if not m:
        err = f"Cannot parse plugin repo entry: {entry!r}"
        raise ValueError(err)
    owner = m.group("owner")
    repo = m.group("repo")
    ref = m.group("ref")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return clone_url, repo, ref


def _clone_or_update(clone_url: str, target: Path, ref: str | None) -> None:
    """Clone the repo if absent, otherwise bring it to the requested ref."""
    if not target.exists():
        cmd = ["git", "clone", "--depth=1"]
        if ref:
            cmd += ["--branch", ref]
        cmd += [clone_url, str(target)]
        logger.info("Cloning plugin repo %s → %s", clone_url, target)
        subprocess.run(cmd, check=True, capture_output=True, text=True)  # noqa: S603
    else:
        logger.info("Plugin dir %s already exists, updating.", target)
        subprocess.run(  # noqa: S603
            ["git", "-C", str(target), "fetch", "--depth=1", "origin"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        if ref:
            subprocess.run(  # noqa: S603
                ["git", "-C", str(target), "checkout", ref],  # noqa: S607
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(  # noqa: S603
                ["git", "-C", str(target), "pull", "--ff-only"],  # noqa: S607
                check=True,
                capture_output=True,
                text=True,
            )


def _check_compatible(plugin_dir: Path) -> bool:
    """Return True iff installing plugin_dir would not break the environment."""
    result = subprocess.run(  # noqa: S603
        ["uv", "pip", "install", "--dry-run", str(plugin_dir)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "Plugin %s is incompatible with the current environment and will be "
            "skipped.\n%s",
            plugin_dir.name,
            result.stderr,
        )
        return False
    return True


def _install(plugin_dir: Path) -> None:
    logger.info("Installing plugin %s (editable).", plugin_dir.name)
    subprocess.run(  # noqa: S603
        ["uv", "pip", "install", "-e", str(plugin_dir)],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )


def load_plugins() -> None:
    """Clone, validate, and install all repos listed in LYRA_PLUGIN_REPOS.

    This function is idempotent: it exits immediately on subsequent calls
    within the same process.

    When the environment variable ``LYRA_PLUGIN_MODE=worker`` is set, git
    operations and the dry-run compatibility check are skipped.  Only
    ``uv pip install -e`` is executed, which is required because each
    container has its own ephemeral Python environment.  If a plugin
    directory does not yet exist on the shared volume the plugin is skipped
    with a warning.
    """
    global _PLUGINS_LOADED  # noqa: PLW0603
    if _PLUGINS_LOADED:
        return
    _PLUGINS_LOADED = True

    raw = os.environ.get("LYRA_PLUGIN_REPOS", "").strip()
    if not raw:
        return

    worker_mode = os.environ.get("LYRA_PLUGIN_MODE", "").strip().lower() == "worker"

    PLUGINS_TARGET_DIR.mkdir(parents=True, exist_ok=True)

    entries = [e.strip() for e in raw.split(",") if e.strip()]
    for entry in entries:
        try:
            clone_url, repo_name, ref = _parse_repo_entry(entry)
        except ValueError:
            logger.warning("Skipping malformed plugin entry: %r", entry)
            continue

        target = PLUGINS_TARGET_DIR / repo_name

        if worker_mode:
            if not target.exists():
                logger.warning(
                    "Plugin %r: directory %s does not exist. Skipping — ensure the "
                    "API server has started and populated the plugin volume first.",
                    repo_name,
                    target,
                )
                continue
        else:
            try:
                _clone_or_update(clone_url, target, ref)
            except subprocess.CalledProcessError as exc:
                logger.warning(
                    "Failed to clone/update plugin %r: %s\n%s",
                    entry,
                    exc,
                    exc.stderr,
                )
                continue

            if not _check_compatible(target):
                continue

        try:
            _install(target)
            logger.info("Plugin %r loaded successfully.", repo_name)
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Failed to install plugin %r: %s\n%s",
                repo_name,
                exc,
                exc.stderr,
            )
