"""Metric registry construction and routing metadata management."""

import hashlib
import json
import logging
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from operator import itemgetter
from pathlib import Path
from typing import Any

from filelock import FileLock
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from lyra.sdk.models.metric import (
    CLIENT_SCHEMA_VERSION,
    JSON_SCHEMA_DIALECT,
    MetricCatalogResponse,
    MetricInfoV4,
    build_metric_search_text,
)
from lyra.sdk.models.plugin_v4 import (
    CompiledMetricManifestV4,
    CompiledPluginManifestV4,
    PluginManifestV4,
    compile_plugin_manifest,
)
from lyra.sdk.types import JsonObject, JsonValue
from pydantic import ValidationError as PydanticValidationError

from lyra_app.config import LyraConfig, get_config
from lyra_app.plugin_state import (
    PluginState,
    PluginStateStore,
    make_repo_record,
    metric_queue_mapping,
    repo_record_to_source,
)
from lyra_app.plugins import MANIFEST_FILENAME, sync_plugin_repos

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricRegistryEntry:
    """Bundle a compiled metric with validation, routing, and catalog metadata."""

    metric: CompiledMetricManifestV4
    plugin_name: str
    plugin_version: str
    request_schema: JsonObject
    request_validator: Validator
    queue: str
    repo_id: str
    catalog_fingerprint: str


@dataclass(frozen=True)
class CatalogRefreshResult:
    """Summarize repository updates and routing changes from a catalog refresh."""

    updated_plugins: list[str]
    previous_catalog_fingerprint: str | None
    catalog_fingerprint: str
    catalog_changed: bool
    assigned_metric_queues: list[str] = field(default_factory=list)
    removed_metric_queues: list[str] = field(default_factory=list)


class MetricPayloadValidationError(Exception):
    """Report structured validation failures for a metric request payload."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        """Initialize the exception with API-compatible validation errors."""
        self.errors = errors
        super().__init__("metric payload validation failed")


TASK_REGISTRY: dict[str, MetricRegistryEntry] = {}
_CATALOG_LOADED = False
_CATALOG_FINGERPRINT: str | None = None


def _empty_catalog_fingerprint() -> str:
    return _fingerprint_payload([])


def _fingerprint_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _normalised_manifest_payload(
    manifests: list[tuple[CompiledPluginManifestV4, Path, str]],
    metric_queues: dict[str, str],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for manifest, _path, repo_id in manifests:
        data = manifest.model_dump(mode="json")
        data["repo_id"] = repo_id
        for metric in data["metrics"]:
            metric["queue"] = metric_queues[metric["name"]]
        data["metrics"] = sorted(data["metrics"], key=itemgetter("name"))
        payload.append(data)
    return sorted(
        payload,
        key=lambda item: (item["plugin"]["name"], item["plugin"]["version"]),
    )


def load_plugin_manifest(path: Path) -> CompiledPluginManifestV4:
    """Load, validate, and compile a repository's version 4 plugin manifest.

    Returns:
        The compiled runtime manifest from the repository root.

    Raises:
        RuntimeError: If the manifest is missing, malformed, or invalid.
    """
    manifest_path = path / MANIFEST_FILENAME
    if not manifest_path.exists():
        msg = f"Plugin repo {path} is missing required {MANIFEST_FILENAME}."
        raise RuntimeError(msg)

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = PluginManifestV4.model_validate(raw)
        return compile_plugin_manifest(manifest)
    except json.JSONDecodeError as exc:
        msg = f"Plugin manifest {manifest_path} is not valid JSON."
        raise RuntimeError(msg) from exc
    except (PydanticValidationError, ValueError) as exc:
        msg = f"Plugin manifest {manifest_path} is invalid: {exc}"
        raise RuntimeError(msg) from exc


def _build_request_validator(metric_name: str, schema: JsonObject) -> Validator:
    validator_class = validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        msg = f"Metric {metric_name!r} has an invalid request_schema: {exc}"
        raise RuntimeError(msg) from exc
    return validator_class(schema)


def _build_registry(
    manifests: list[tuple[CompiledPluginManifestV4, Path, str]],
    metric_queues: dict[str, str],
) -> dict[str, MetricRegistryEntry]:
    catalog_fingerprint = public_catalog_fingerprint(
        [
            _metric_info_from_manifest(metric)
            for manifest, _path, _repo_id in manifests
            for metric in manifest.metrics
        ]
    )
    registry: dict[str, MetricRegistryEntry] = {}
    for manifest, _path, repo_id in manifests:
        for metric in manifest.metrics:
            if metric.name in registry:
                msg = f"Duplicate metric name in plugin manifests: {metric.name!r}"
                raise RuntimeError(msg)
            try:
                queue = metric_queues[metric.name]
            except KeyError as exc:
                msg = f"Metric {metric.name!r} does not have a queue assignment."
                raise RuntimeError(msg) from exc
            request_schema = metric.request_schema
            registry[metric.name] = MetricRegistryEntry(
                metric=metric,
                plugin_name=manifest.plugin.name,
                plugin_version=manifest.plugin.version,
                request_schema=request_schema,
                request_validator=_build_request_validator(metric.name, request_schema),
                queue=queue,
                repo_id=repo_id,
                catalog_fingerprint=catalog_fingerprint,
            )
    return registry


def sync_catalog_state_repos(config: LyraConfig, state: PluginState) -> list[Any]:
    """Synchronize enabled state repositories into the local plugin catalog.

    Returns:
        Synchronization results for every enabled repository source.
    """
    raw_entries = [repo_record_to_source(repo) for repo in state.repos if repo.enabled]
    return sync_plugin_repos(
        config.plugins.catalog_dir,
        raw_entries,
        raise_on_error=True,
    )


def _enabled_repo_ids_by_source(state: PluginState) -> dict[str, str]:
    return {
        repo_record_to_source(repo): repo.id for repo in state.repos if repo.enabled
    }


def _synced_repo_id(raw_source: str, repo_ids_by_source: dict[str, str]) -> str:
    try:
        return repo_ids_by_source[raw_source]
    except KeyError as exc:
        msg = f"Synced plugin source {raw_source!r} is not in enabled plugin state."
        raise RuntimeError(msg) from exc


def refresh_catalog(
    store: PluginStateStore | None = None,
    *,
    config: LyraConfig | None = None,
) -> CatalogRefreshResult:
    """Synchronize plugins, reconcile routes, and atomically replace the registry.

    Returns:
        Repository updates, catalog identity changes, and routing changes.
    """
    global _CATALOG_FINGERPRINT, _CATALOG_LOADED  # ruff:ignore[global-statement]

    previous_fingerprint = _CATALOG_FINGERPRINT
    config = get_config() if config is None else config
    state_store = store or PluginStateStore(
        allowed_queues=config.plugins.allowed_queues,
    )
    state = state_store.load()
    synced = sync_catalog_state_repos(config, state)
    repo_ids_by_source = _enabled_repo_ids_by_source(state)
    manifests = [
        (
            load_plugin_manifest(repo.path),
            repo.path,
            _synced_repo_id(repo.entry.raw, repo_ids_by_source),
        )
        for repo in synced
    ]
    metric_repo_ids = {
        metric.name: repo_id
        for manifest, _path, repo_id in manifests
        for metric in manifest.metrics
    }
    queue_sync = state_store.sync_metric_queues(
        metric_repo_ids,
        default_queue=config.plugins.default_queue,
    )
    if queue_sync.assigned or queue_sync.removed:
        state = state_store.reload()
    metric_queues = metric_queue_mapping(state)

    fingerprint = _fingerprint_payload(
        _normalised_manifest_payload(manifests, metric_queues)
    )
    TASK_REGISTRY.clear()
    TASK_REGISTRY.update(_build_registry(manifests, metric_queues))
    _CATALOG_FINGERPRINT = fingerprint
    _CATALOG_LOADED = True

    updated = [repo.entry.display_name for repo in synced if repo.changed]
    catalog_changed = previous_fingerprint != fingerprint
    logger.info(
        "Loaded %d state-backed metric manifest(s); catalog fingerprint=%s; changed=%s",
        len(TASK_REGISTRY),
        fingerprint,
        catalog_changed,
    )
    return CatalogRefreshResult(
        updated_plugins=updated,
        previous_catalog_fingerprint=previous_fingerprint,
        catalog_fingerprint=fingerprint,
        catalog_changed=catalog_changed,
        assigned_metric_queues=queue_sync.assigned,
        removed_metric_queues=queue_sync.removed,
    )


def refresh_catalog_from_state(
    store: PluginStateStore | None = None,
) -> CatalogRefreshResult:
    """Refresh the metric catalog using persisted plugin state.

    Returns:
        Repository, catalog, and routing changes from the refresh.
    """
    return refresh_catalog(store)


def initialize_catalog(
    config: LyraConfig | None = None,
    *,
    store: PluginStateStore | None = None,
) -> CatalogRefreshResult:
    """Seed missing plugin state and load the initial metric catalog.

    Returns:
        Repository, catalog, and routing changes from initial loading.
    """
    config = get_config() if config is None else config
    state_store = store or PluginStateStore(
        allowed_queues=config.plugins.allowed_queues,
    )
    state_path = state_store.path
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(f"{state_path}.lock"):
        if state_path.exists():
            return refresh_catalog(state_store, config=config)

        initial_state = PluginState(
            repos=[make_repo_record(source) for source in config.plugins.initial_repos]
        )
        with tempfile.NamedTemporaryFile(
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            suffix=".initial",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
        temp_path.unlink()
        temp_store = type(state_store)(
            temp_path,
            allowed_queues=state_store.allowed_queues,
        )

        try:
            temp_store.save(initial_state)
            result = refresh_catalog(temp_store, config=config)
            temp_path.replace(state_path)
        finally:
            temp_path.unlink(missing_ok=True)

        return result


def ensure_catalog_loaded() -> None:
    """Initialize the catalog on first access."""
    if not _CATALOG_LOADED:
        initialize_catalog()


def get_catalog_fingerprint() -> str:
    """Return the internal fingerprint after ensuring the catalog is loaded."""
    ensure_catalog_loaded()
    return _CATALOG_FINGERPRINT or _empty_catalog_fingerprint()


def get_loaded_catalog_fingerprint() -> str:
    """Return the current internal fingerprint without loading the catalog."""
    return _CATALOG_FINGERPRINT or _empty_catalog_fingerprint()


def _public_metric_payload(metrics: list[MetricInfoV4]) -> list[dict[str, Any]]:
    return [
        metric.model_dump(mode="json")
        for metric in sorted(metrics, key=lambda item: item.name)
    ]


def public_catalog_fingerprint(metrics: list[MetricInfoV4]) -> str:
    """Compute a stable fingerprint of the public metric catalog contract.

    Returns:
        The SHA-256 digest of the sorted public catalog payload.
    """
    return _fingerprint_payload(
        {
            "client_schema_version": CLIENT_SCHEMA_VERSION,
            "json_schema_dialect": JSON_SCHEMA_DIALECT,
            "metrics": _public_metric_payload(metrics),
        }
    )


def get_public_catalog_fingerprint() -> str:
    """Return the fingerprint of the currently exposed public metric catalog."""
    return public_catalog_fingerprint(get_metrics_info())


def is_catalog_loaded() -> bool:
    """Return whether the process has initialized its metric registry."""
    return _CATALOG_LOADED


def get_loaded_metric_names() -> list[str]:
    """Return sorted metric names without triggering catalog initialization."""
    return sorted(TASK_REGISTRY)


def get_loaded_metric_queues() -> dict[str, str]:
    """Return loaded metric-to-queue assignments without initializing state."""
    return {
        metric_name: entry.queue for metric_name, entry in sorted(TASK_REGISTRY.items())
    }


def get_metric_entry(name: str) -> MetricRegistryEntry | None:
    """Return a metric registry entry after ensuring the catalog is loaded."""
    ensure_catalog_loaded()
    return TASK_REGISTRY.get(name)


def get_metric_info(name: str) -> MetricInfoV4 | None:
    """Return public metadata for a named metric when it exists."""
    entry = get_metric_entry(name)
    if entry is None:
        return None
    return _metric_info_from_entry(entry)


def get_metrics_info() -> list[MetricInfoV4]:
    """Return public metadata for all registered metrics in name order."""
    ensure_catalog_loaded()
    return [
        _metric_info_from_entry(entry) for _name, entry in sorted(TASK_REGISTRY.items())
    ]


def get_metric_search_text(name: str) -> str | None:
    """Build normalized discovery text for a registered metric.

    Returns:
        Searchable text for the metric, or ``None`` when it is unknown.
    """
    info = get_metric_info(name)
    if info is None:
        return None
    return build_metric_search_text(info)


def get_metric_catalog() -> MetricCatalogResponse:
    """Build the complete versioned public metric catalog response.

    Returns:
        Catalog schema metadata, fingerprint, and all public metrics.
    """
    metrics = get_metrics_info()
    return MetricCatalogResponse(
        client_schema_version=CLIENT_SCHEMA_VERSION,
        json_schema_dialect=JSON_SCHEMA_DIALECT,
        catalog_fingerprint=public_catalog_fingerprint(metrics),
        metrics=metrics,
    )


def validate_metric_payload(metric_name: str, payload: JsonValue) -> JsonObject:
    """Validate and defensively copy a request for a named metric.

    Returns:
        A deep copy of the validated JSON object.

    Raises:
        KeyError: If no registered metric has the requested name.
    """
    entry = get_metric_entry(metric_name)
    if entry is None:
        msg = f"Unknown metric: {metric_name!r}"
        raise KeyError(msg)
    return validate_metric_entry_payload(entry, payload)


def validate_metric_entry_payload(
    entry: MetricRegistryEntry,
    payload: JsonValue,
) -> JsonObject:
    """Validate a payload against one captured registry contract.

    Returns:
        A deep copy of the validated JSON object.

    Raises:
        MetricPayloadValidationError: If the value is not an object, violates the
            request schema, or repeats a batch key.
    """
    if not isinstance(payload, dict):
        raise MetricPayloadValidationError(
            [{"loc": [], "msg": "Input must be a JSON object.", "type": "type"}],
        )

    errors = sorted(
        entry.request_validator.iter_errors(payload),
        key=lambda error: list(error.path),
    )
    if errors:
        raise MetricPayloadValidationError(
            [_format_validation_error(error) for error in errors]
        )
    batch_errors = _validate_unique_batch_keys(entry.metric, payload)
    if batch_errors:
        raise MetricPayloadValidationError(batch_errors)
    return deepcopy(payload)


def _validate_unique_batch_keys(
    metric: CompiledMetricManifestV4,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for field_name in metric.batch_inputs:
        source_values = payload[field_name]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for source_value in source_values:
            key = source_value["key"]
            if key in seen:
                duplicates.add(key)
            seen.add(key)

        if duplicates:
            duplicate_names = ", ".join(sorted(duplicates))
            errors.append(
                {
                    "loc": [field_name],
                    "msg": f"Batch input keys must be unique: {duplicate_names}.",
                    "type": "unique_batch_keys",
                }
            )
    return errors


def _format_validation_error(error: JsonSchemaValidationError) -> dict[str, Any]:
    return {
        "loc": list(error.path),
        "msg": error.message,
        "type": str(error.validator),
    }


def _metric_info_from_entry(entry: MetricRegistryEntry) -> MetricInfoV4:
    return _metric_info_from_manifest(entry.metric)


def _metric_info_from_manifest(metric: CompiledMetricManifestV4) -> MetricInfoV4:
    return MetricInfoV4(
        name=metric.name,
        description=metric.description.strip(),
        request_schema=metric.request_schema,
        spatial_inputs=metric.spatial_inputs,
        output=metric.output,
    )


def reload_tasks() -> None:
    """Refresh task metadata from current plugin state."""
    refresh_catalog()


def reset_catalog() -> None:
    """Clear all loaded metrics and catalog initialization state."""
    global _CATALOG_FINGERPRINT, _CATALOG_LOADED  # ruff:ignore[global-statement]

    TASK_REGISTRY.clear()
    _CATALOG_FINGERPRINT = None
    _CATALOG_LOADED = False
