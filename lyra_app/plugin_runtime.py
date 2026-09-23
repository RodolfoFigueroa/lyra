"""Derived plugin snapshots shared by API startup and worker launchers."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from lyra_app.config import LyraConfig


class SourceSnapshot(BaseModel):
    """Identify a source captured by the API before worker installation."""

    model_config = ConfigDict(extra="forbid")
    repo_id: str
    path: Path
    resolved_ref: str | None = None
    content_hash: str


class StartupSnapshot(BaseModel):
    """Describe derived artifacts for one successful API initialization."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["initializing", "ready", "failed"]
    config_fingerprint: str
    sources: list[SourceSnapshot] = Field(default_factory=list)
    metric_queues: dict[str, str] = Field(default_factory=dict)
    catalog_fingerprint: str | None = None


def config_fingerprint(config: LyraConfig) -> str:
    """Hash loaded nonsecret settings for API/worker consistency checks.

    Returns:
        A stable SHA-256 fingerprint.
    """
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def snapshot_path(config: LyraConfig) -> Path:
    """Locate the derived startup descriptor.

    Returns:
        The descriptor path below the catalog cache.
    """
    return config.plugins.catalog_dir / "startup.json"


def publish_snapshot(config: LyraConfig, snapshot: StartupSnapshot) -> None:
    """Atomically replace the descriptor while the caller holds its lock."""
    path = Path(snapshot_path(config))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(snapshot.model_dump_json(), encoding="utf-8")
    temporary.replace(path)


def read_snapshot(config: LyraConfig) -> StartupSnapshot:
    """Read a ready descriptor matching the worker's startup configuration.

    Returns:
        The validated API snapshot.

    Raises:
        RuntimeError: If the API has not prepared a matching catalog.
    """
    try:
        snapshot = StartupSnapshot.model_validate_json(
            snapshot_path(config).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        msg = (
            "API plugin snapshot is unavailable; start the API and wait for readiness."
        )
        raise RuntimeError(msg) from exc
    if snapshot.status != "ready" or snapshot.config_fingerprint != config_fingerprint(
        config
    ):
        msg = (
            "API plugin snapshot is not ready or configuration differs; "
            "restart the API and workers together."
        )
        raise RuntimeError(msg)
    return snapshot


def source_hash(path: Path) -> str:
    """Fingerprint prepared source files without Git internals.

    Returns:
        A stable SHA-256 source fingerprint.
    """
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        if item.is_file():
            digest.update(relative.as_posix().encode())
            digest.update(b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def copy_source(source: Path, destination: Path) -> None:
    """Copy a startup source into a fresh private worker directory."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source, destination, ignore=shutil.ignore_patterns(".git", "__pycache__")
    )
