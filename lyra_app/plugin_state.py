from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from lyra_app.config import LYRA_DATA_DIR
from lyra_app.plugins import parse_repo_entry

if TYPE_CHECKING:
    from collections.abc import Iterable

    from lyra_app.plugins import RepoSourceKind

DEFAULT_PLUGIN_STATE_PATH = LYRA_DATA_DIR / "state" / "plugins.toml"
PLUGIN_STATE_SCHEMA_VERSION = 1

_BARE_TOML_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_REPO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class PluginStateError(RuntimeError):
    """Base error for plugin state operations."""


class PluginStateLoadError(PluginStateError):
    """Raised when the plugin state file cannot be loaded or validated."""


class PluginStateValidationError(PluginStateError):
    """Raised when a plugin state mutation would violate the state contract."""


class PluginStateNotFoundError(PluginStateError, KeyError):
    """Raised when an addressed plugin state record does not exist."""


class StrictPluginStateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class NormalizedRepoSource:
    source: str
    ref: str | None
    source_kind: RepoSourceKind
    generated_id: str


def _strip_required_string(value: Any, *, field_name: str) -> Any:
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        msg = f"{field_name} must be a non-empty string"
        raise ValueError(msg)
    return stripped


def _strip_optional_string(value: Any, *, field_name: str) -> Any:
    if value is None or not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        msg = f"{field_name} must be a non-empty string when provided"
        raise ValueError(msg)
    return stripped


def _strip_string_mapping(value: Any, *, key_label: str, value_label: str) -> Any:
    if not isinstance(value, dict):
        return value

    stripped_items: dict[Any, Any] = {}
    for raw_key, raw_value in value.items():
        key = _strip_required_string(raw_key, field_name=key_label)
        if key in stripped_items:
            msg = f"duplicate {key_label} after trimming whitespace: {key!r}"
            raise ValueError(msg)
        stripped_items[key] = _strip_required_string(
            raw_value,
            field_name=value_label,
        )
    return stripped_items


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_key(value: str) -> str:
    return value if _BARE_TOML_KEY_PATTERN.fullmatch(value) else _toml_string(value)


def normalize_repo_source(raw_source: str) -> NormalizedRepoSource:
    entry = parse_repo_entry(raw_source)
    if entry.source_kind == "local":
        if entry.source_path is None:
            msg = f"Local plugin repo source could not be resolved: {raw_source!r}"
            raise ValueError(msg)
        return NormalizedRepoSource(
            source=entry.source_path.as_uri(),
            ref=None,
            source_kind=entry.source_kind,
            generated_id=entry.target_name,
        )
    if entry.source_kind == "directory":
        if entry.source_path is None:
            msg = f"Directory plugin source could not be resolved: {raw_source!r}"
            raise ValueError(msg)
        return NormalizedRepoSource(
            source=entry.clone_url,
            ref=None,
            source_kind=entry.source_kind,
            generated_id=entry.target_name,
        )

    return NormalizedRepoSource(
        source=f"{entry.owner}/{entry.repo}",
        ref=entry.ref,
        source_kind=entry.source_kind,
        generated_id=entry.target_name,
    )


def generate_repo_id(source: str) -> str:
    return normalize_repo_source(source).generated_id


def repo_record_to_source(repo: PluginRepoRecord) -> str:
    if repo.ref is None:
        return repo.source
    return f"{repo.source}@{repo.ref}"


class PluginRepoRecord(StrictPluginStateModel):
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    ref: str | None = None
    enabled: bool = True

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> Any:
        return _strip_required_string(value, field_name="repos.id")

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _REPO_ID_PATTERN.fullmatch(value):
            msg = "repos.id may only contain A-Z, a-z, 0-9, underscore, dot, or dash"
            raise ValueError(msg)
        return value

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: Any) -> Any:
        return _strip_required_string(value, field_name="repos.source")

    @field_validator("ref", mode="before")
    @classmethod
    def normalize_ref(cls, value: Any) -> Any:
        return _strip_optional_string(value, field_name="repos.ref")

    @model_validator(mode="after")
    def validate_normalized_source(self) -> Self:
        try:
            normalized = normalize_repo_source(self.source)
        except ValueError as exc:
            msg = f"malformed repo source {self.source!r}: {exc}"
            raise ValueError(msg) from exc

        if normalized.ref is not None:
            msg = "repos.source must not include an inline ref"
            raise ValueError(msg)

        if normalized.source != self.source:
            msg = "repos.source must be normalized before it is stored"
            raise ValueError(msg)

        if normalized.source_kind == "local" and self.ref is not None:
            msg = "local plugin repo sources cannot include refs"
            raise ValueError(msg)

        if normalized.source_kind == "directory" and self.ref is not None:
            msg = "directory plugin sources cannot include refs"
            raise ValueError(msg)

        return self


class PluginState(StrictPluginStateModel):
    schema_version: Literal[1] = PLUGIN_STATE_SCHEMA_VERSION
    repos: list[PluginRepoRecord] = Field(default_factory=list)
    metric_queues: dict[str, str] = Field(default_factory=dict)

    @field_validator("metric_queues", mode="before")
    @classmethod
    def normalize_metric_queues(cls, value: Any) -> Any:
        return _strip_string_mapping(
            value,
            key_label="metric name",
            value_label="queue name",
        )

    @model_validator(mode="after")
    def validate_repos(self) -> Self:
        seen_ids: set[str] = set()
        duplicate_ids: set[str] = set()
        enabled_sources: set[str] = set()
        duplicate_enabled_sources: set[str] = set()

        for repo in self.repos:
            if repo.id in seen_ids:
                duplicate_ids.add(repo.id)
            seen_ids.add(repo.id)

            if repo.enabled:
                if repo.source in enabled_sources:
                    duplicate_enabled_sources.add(repo.source)
                enabled_sources.add(repo.source)

        if duplicate_ids:
            names = ", ".join(sorted(duplicate_ids))
            msg = f"duplicate plugin repo IDs: {names}"
            raise ValueError(msg)

        if duplicate_enabled_sources:
            names = ", ".join(sorted(duplicate_enabled_sources))
            msg = f"duplicate enabled plugin repo sources: {names}"
            raise ValueError(msg)

        return self

    @classmethod
    def empty(cls) -> PluginState:
        return cls()


def make_repo_record(
    source: str,
    *,
    repo_id: str | None = None,
    enabled: bool = True,
) -> PluginRepoRecord:
    normalized = normalize_repo_source(source)
    selected_id = repo_id if repo_id is not None else normalized.generated_id
    return PluginRepoRecord(
        id=selected_id,
        source=normalized.source,
        ref=normalized.ref,
        enabled=enabled,
    )


def validate_plugin_state(
    state: PluginState,
    *,
    allowed_queues: Iterable[str],
) -> None:
    allowed = set(allowed_queues)
    invalid_queues = sorted(
        {queue for queue in state.metric_queues.values() if queue not in allowed}
    )
    if invalid_queues:
        names = ", ".join(invalid_queues)
        msg = f"metric queue names must appear in plugins.allowed_queues: {names}"
        raise PluginStateValidationError(msg)


def _state_payload(state: PluginState) -> dict[str, Any]:
    return {
        "schema_version": state.schema_version,
        "repos": [repo.model_dump(mode="json") for repo in state.repos],
        "metric_queues": dict(state.metric_queues),
    }


def _validated_state(payload: dict[str, Any]) -> PluginState:
    try:
        return PluginState.model_validate(payload)
    except ValidationError as exc:
        msg = f"plugin state failed validation: {exc}"
        raise PluginStateValidationError(msg) from exc


def render_plugin_state_toml(state: PluginState) -> str:
    lines = [f"schema_version = {PLUGIN_STATE_SCHEMA_VERSION}", ""]

    for repo in state.repos:
        lines.append("[[repos]]")
        lines.append(f"id = {_toml_string(repo.id)}")
        lines.append(f"source = {_toml_string(repo.source)}")
        if repo.ref is not None:
            lines.append(f"ref = {_toml_string(repo.ref)}")
        lines.append(f"enabled = {str(repo.enabled).lower()}")
        lines.append("")

    lines.append("[metric_queues]")
    for metric_name, queue_name in sorted(state.metric_queues.items()):
        lines.append(f"{_toml_key(metric_name)} = {_toml_string(queue_name)}")

    return "\n".join(lines).rstrip() + "\n"


def load_plugin_state(
    path: str | Path = DEFAULT_PLUGIN_STATE_PATH,
    *,
    allowed_queues: Iterable[str],
) -> PluginState:
    state_path = Path(path)
    try:
        with state_path.open("rb") as state_file:
            raw_state = tomllib.load(state_file)
    except FileNotFoundError:
        state = PluginState.empty()
        validate_plugin_state(state, allowed_queues=allowed_queues)
        return state
    except tomllib.TOMLDecodeError as exc:
        msg = f"Plugin state file is not valid TOML: {state_path}: {exc}"
        raise PluginStateLoadError(msg) from exc
    except OSError as exc:
        msg = f"Plugin state file could not be read: {state_path}"
        raise PluginStateLoadError(msg) from exc

    try:
        state = PluginState.model_validate(raw_state)
        validate_plugin_state(state, allowed_queues=allowed_queues)
    except (ValidationError, PluginStateValidationError) as exc:
        msg = f"Plugin state file failed validation: {state_path}: {exc}"
        raise PluginStateLoadError(msg) from exc

    return state


def save_plugin_state(
    state: PluginState,
    path: str | Path = DEFAULT_PLUGIN_STATE_PATH,
    *,
    allowed_queues: Iterable[str],
) -> None:
    validate_plugin_state(state, allowed_queues=allowed_queues)

    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = render_plugin_state_toml(state)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.replace(state_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


class PluginStateStore:
    def __init__(
        self,
        path: str | Path = DEFAULT_PLUGIN_STATE_PATH,
        *,
        allowed_queues: Iterable[str],
    ) -> None:
        self.path = Path(path)
        self.allowed_queues = tuple(allowed_queues)

    def load(self) -> PluginState:
        return load_plugin_state(self.path, allowed_queues=self.allowed_queues)

    def reload(self) -> PluginState:
        return self.load()

    def save(self, state: PluginState) -> None:
        save_plugin_state(state, self.path, allowed_queues=self.allowed_queues)

    def add_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> PluginRepoRecord:
        state = self.load()
        repo = make_repo_record(source, repo_id=repo_id, enabled=enabled)
        if any(existing.id == repo.id for existing in state.repos):
            msg = f"plugin repo id already exists: {repo.id}; provide a unique id"
            raise PluginStateValidationError(msg)

        candidate = _validated_state(
            _state_payload(state) | {"repos": [*state.repos, repo]}
        )
        self.save(candidate)
        return repo

    def update_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> PluginRepoRecord:
        repo_id = _strip_required_string(repo_id, field_name="repo_id")
        if not isinstance(repo_id, str):
            msg = "repo_id must be a string"
            raise TypeError(msg)

        state = self.load()
        repos = list(state.repos)
        for index, existing in enumerate(repos):
            if existing.id != repo_id:
                continue

            updates = existing.model_dump(mode="json")
            if source is not None:
                normalized = normalize_repo_source(source)
                updates["source"] = normalized.source
                updates["ref"] = normalized.ref
            if enabled is not None:
                updates["enabled"] = enabled

            updated = PluginRepoRecord.model_validate(updates)
            repos[index] = updated
            candidate = _validated_state(_state_payload(state) | {"repos": repos})
            self.save(candidate)
            return updated

        msg = f"unknown plugin repo id: {repo_id}"
        raise PluginStateNotFoundError(msg)

    def delete_repo(self, repo_id: str) -> bool:
        repo_id = _strip_required_string(repo_id, field_name="repo_id")
        if not isinstance(repo_id, str):
            msg = "repo_id must be a string"
            raise TypeError(msg)

        state = self.load()
        repos = [repo for repo in state.repos if repo.id != repo_id]
        if len(repos) == len(state.repos):
            return False

        candidate = _validated_state(_state_payload(state) | {"repos": repos})
        self.save(candidate)
        return True

    def set_metric_queue(self, metric_name: str, queue: str) -> str:
        metric_name = _strip_required_string(metric_name, field_name="metric name")
        queue = _strip_required_string(queue, field_name="queue name")
        if not isinstance(metric_name, str) or not isinstance(queue, str):
            msg = "metric_name and queue must be strings"
            raise TypeError(msg)

        state = self.load()
        metric_queues = dict(state.metric_queues)
        metric_queues[metric_name] = queue
        candidate = _validated_state(
            _state_payload(state) | {"metric_queues": metric_queues}
        )
        self.save(candidate)
        return queue

    def delete_metric_queue(self, metric_name: str) -> bool:
        metric_name = _strip_required_string(metric_name, field_name="metric name")
        if not isinstance(metric_name, str):
            msg = "metric_name must be a string"
            raise TypeError(msg)

        state = self.load()
        if metric_name not in state.metric_queues:
            return False

        metric_queues = dict(state.metric_queues)
        del metric_queues[metric_name]
        candidate = _validated_state(
            _state_payload(state) | {"metric_queues": metric_queues}
        )
        self.save(candidate)
        return True

    def assign_missing_metric_queues(
        self,
        metric_names: Iterable[str],
        *,
        default_queue: str,
    ) -> list[str]:
        default_queue = _strip_required_string(
            default_queue,
            field_name="default queue",
        )
        if not isinstance(default_queue, str):
            msg = "default_queue must be a string"
            raise TypeError(msg)

        state = self.load()
        normalized_metric_names = {
            _strip_required_string(metric_name, field_name="metric name")
            for metric_name in metric_names
        }
        if not all(
            isinstance(metric_name, str) for metric_name in normalized_metric_names
        ):
            msg = "metric names must be strings"
            raise TypeError(msg)

        missing = sorted(normalized_metric_names - set(state.metric_queues))
        if not missing:
            return []

        metric_queues = dict(state.metric_queues)
        for metric_name in missing:
            metric_queues[metric_name] = default_queue

        candidate = _validated_state(
            _state_payload(state) | {"metric_queues": metric_queues}
        )
        self.save(candidate)
        return missing


__all__ = [
    "DEFAULT_PLUGIN_STATE_PATH",
    "PLUGIN_STATE_SCHEMA_VERSION",
    "NormalizedRepoSource",
    "PluginRepoRecord",
    "PluginState",
    "PluginStateError",
    "PluginStateLoadError",
    "PluginStateNotFoundError",
    "PluginStateStore",
    "PluginStateValidationError",
    "generate_repo_id",
    "load_plugin_state",
    "make_repo_record",
    "normalize_repo_source",
    "render_plugin_state_toml",
    "repo_record_to_source",
    "save_plugin_state",
    "validate_plugin_state",
]
