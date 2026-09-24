"""Offline parsing of configured plugin sources."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

PluginSourceKind = Literal["github", "local", "directory"]


@dataclass(frozen=True)
class PluginSource:
    """Identify a GitHub repository or an absolute local source location."""

    kind: PluginSourceKind
    location: str

    @property
    def canonical(self) -> str:
        """The canonical spelling stored in configuration."""
        if self.kind == "github":
            return self.location
        scheme = "file" if self.kind == "local" else "dir"
        return f"{scheme}://{quote(self.location, safe='/')}"

    @property
    def path(self) -> Path | None:
        """The local path, without filesystem access or symlink resolution."""
        return None if self.kind == "github" else Path(self.location)

    @property
    def git_url(self) -> str:
        """The Git fetch URL for a repository source.

        Raises:
            ValueError: If this source is a plain directory.
        """
        if self.kind == "directory":
            msg = "Directory plugin sources do not have a Git URL."
            raise ValueError(msg)
        if self.kind == "github":
            return f"https://github.com/{self.location}.git"
        return self.canonical


def parse_plugin_source(value: str) -> PluginSource:
    """Parse and normalize a source without accessing files or external services.

    Returns:
        The source kind and normalized location.

    Raises:
        ValueError: If the source syntax is unsupported or malformed.
    """
    if not value or value != value.strip() or any(ord(char) < 32 for char in value):
        msg = (
            "Plugin sources must be nonblank without surrounding whitespace "
            "or controls."
        )
        raise ValueError(msg)
    if value.startswith(("file://", "dir://")):
        parsed = urlsplit(value)
        path = unquote(parsed.path, errors="strict")
        if (
            parsed.netloc not in {"", "localhost"}
            or any(delimiter in value for delimiter in ("?", "#"))
            or re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path)
            or not Path(path).is_absolute()
            or any(ord(char) < 32 for char in path)
        ):
            msg = (
                "Local plugin sources require absolute file:// or dir:// paths "
                "without queries or fragments."
            )
            raise ValueError(msg)
        return PluginSource(
            kind="local" if parsed.scheme == "file" else "directory",
            location=Path(path).as_posix(),
        )
    repository = value.removeprefix("https://github.com/").removesuffix(".git")
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or repository.endswith(".git")
        or any(part in {".", ".."} for part in repository.split("/"))
    ):
        msg = (
            "Plugin source must be owner/repo, a GitHub HTTPS URL, or an absolute "
            "file:// or dir:// URI; specify revisions in ref."
        )
        raise ValueError(msg)
    return PluginSource(kind="github", location=repository)
