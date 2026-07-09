import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfoV3
from lyra.sdk.models.plugin_v3 import (
    CompiledMetricManifestV3,
    CompiledPluginManifestV3,
    PluginManifestV3,
    compile_plugin_manifest,
)
from pydantic import ValidationError as PydanticValidationError

from lyra_app.config import LyraConfig, get_config
from lyra_app.plugin_state import (
    PluginState,
    PluginStateStore,
    metric_queue_mapping,
    repo_record_to_source,
)
from lyra_app.plugins import MANIFEST_FILENAME, sync_plugin_repos

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricRegistryEntry:
    metric: CompiledMetricManifestV3
    plugin_name: str
    plugin_version: str
    request_schema: dict[str, Any]
    request_validator: Any
    queue: str
    repo_id: str
    entrypoint: str


@dataclass(frozen=True)
class CatalogRefreshResult:
    updated_plugins: list[str]
    previous_catalog_fingerprint: str | None
    catalog_fingerprint: str
    catalog_changed: bool
    assigned_metric_queues: list[str] = field(default_factory=list)
    removed_metric_queues: list[str] = field(default_factory=list)


class MetricPayloadValidationError(Exception):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__("metric payload validation failed")


TASK_REGISTRY: dict[str, MetricRegistryEntry] = {}
_CATALOG_LOADED = False
_CATALOG_FINGERPRINT: str | None = None


def _empty_catalog_fingerprint() -> str:
    return _fingerprint_payload([])


def _fingerprint_payload(payload: list[dict[str, Any]]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _normalised_manifest_payload(
    manifests: list[tuple[CompiledPluginManifestV3, Path, str]],
    metric_queues: dict[str, str],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for manifest, _path, repo_id in manifests:
        data = manifest.model_dump(mode="json")
        data["repo_id"] = repo_id
        for metric in data["metrics"]:
            metric["queue"] = metric_queues[metric["name"]]
        data["metrics"] = sorted(data["metrics"], key=lambda item: item["name"])
        payload.append(data)
    return sorted(
        payload,
        key=lambda item: (item["plugin"]["name"], item["plugin"]["version"]),
    )


def load_plugin_manifest(path: Path) -> CompiledPluginManifestV3:
    manifest_path = path / MANIFEST_FILENAME
    if not manifest_path.exists():
        msg = f"Plugin repo {path} is missing required {MANIFEST_FILENAME}."
        raise RuntimeError(msg)

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = PluginManifestV3.model_validate(raw)
        return compile_plugin_manifest(manifest)
    except json.JSONDecodeError as exc:
        msg = f"Plugin manifest {manifest_path} is not valid JSON."
        raise RuntimeError(msg) from exc
    except (PydanticValidationError, ValueError) as exc:
        msg = f"Plugin manifest {manifest_path} is invalid: {exc}"
        raise RuntimeError(msg) from exc


def _build_request_validator(metric_name: str, schema: dict[str, Any]) -> Any:
    validator_class = validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        msg = f"Metric {metric_name!r} has an invalid request_schema: {exc}"
        raise RuntimeError(msg) from exc
    return validator_class(schema)


def _build_registry(
    manifests: list[tuple[CompiledPluginManifestV3, Path, str]],
    metric_queues: dict[str, str],
) -> dict[str, MetricRegistryEntry]:
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
                entrypoint=metric.entrypoint,
            )
    return registry


def sync_catalog_state_repos(config: LyraConfig, state: PluginState) -> list[Any]:
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
) -> CatalogRefreshResult:
    global _CATALOG_FINGERPRINT, _CATALOG_LOADED  # noqa: PLW0603

    previous_fingerprint = _CATALOG_FINGERPRINT
    config = get_config()
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

    registry = _build_registry(manifests, metric_queues)

    fingerprint = _fingerprint_payload(
        _normalised_manifest_payload(manifests, metric_queues)
    )
    TASK_REGISTRY.clear()
    TASK_REGISTRY.update(registry)
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
    return refresh_catalog(store)


def ensure_catalog_loaded() -> None:
    if not _CATALOG_LOADED:
        refresh_catalog()


def get_catalog_fingerprint() -> str:
    ensure_catalog_loaded()
    return _CATALOG_FINGERPRINT or _empty_catalog_fingerprint()


def get_loaded_catalog_fingerprint() -> str:
    return _CATALOG_FINGERPRINT or _empty_catalog_fingerprint()


def _public_metric_payload(metrics: list[MetricInfoV3]) -> list[dict[str, Any]]:
    return [
        metric.model_dump(mode="json")
        for metric in sorted(metrics, key=lambda item: item.name)
    ]


def public_catalog_fingerprint(metrics: list[MetricInfoV3]) -> str:
    return _fingerprint_payload(_public_metric_payload(metrics))


def get_public_catalog_fingerprint() -> str:
    return public_catalog_fingerprint(get_metrics_info())


def is_catalog_loaded() -> bool:
    return _CATALOG_LOADED


def get_loaded_metric_names() -> list[str]:
    return sorted(TASK_REGISTRY)


def get_loaded_metric_queues() -> dict[str, str]:
    return {
        metric_name: entry.queue for metric_name, entry in sorted(TASK_REGISTRY.items())
    }


def get_metric_entry(name: str) -> MetricRegistryEntry | None:
    ensure_catalog_loaded()
    return TASK_REGISTRY.get(name)


def get_metric_info(name: str) -> MetricInfoV3 | None:
    entry = get_metric_entry(name)
    if entry is None:
        return None
    return _metric_info_from_entry(entry)


def get_metrics_info() -> list[MetricInfoV3]:
    ensure_catalog_loaded()
    return [
        _metric_info_from_entry(entry) for _name, entry in sorted(TASK_REGISTRY.items())
    ]


def get_metric_catalog() -> MetricCatalogResponse:
    metrics = get_metrics_info()
    return MetricCatalogResponse(
        catalog_fingerprint=public_catalog_fingerprint(metrics),
        metrics=metrics,
    )


def validate_metric_payload(metric_name: str, payload: Any) -> dict[str, Any]:
    entry = get_metric_entry(metric_name)
    if entry is None:
        msg = f"Unknown metric: {metric_name!r}"
        raise KeyError(msg)
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
    return payload


def _validate_unique_batch_keys(
    metric: CompiledMetricManifestV3,
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


def _metric_info_from_entry(entry: MetricRegistryEntry) -> MetricInfoV3:
    return MetricInfoV3(
        name=entry.metric.name,
        description=entry.metric.description.strip(),
        request_schema=entry.request_schema,
        output=entry.metric.output,
    )


def reload_tasks() -> None:
    refresh_catalog()


def reset_catalog() -> None:
    global _CATALOG_FINGERPRINT, _CATALOG_LOADED  # noqa: PLW0603

    TASK_REGISTRY.clear()
    _CATALOG_FINGERPRINT = None
    _CATALOG_LOADED = False
