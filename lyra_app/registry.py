"""Metric registry construction and routing metadata management."""

import hashlib
import json
import logging
import shutil
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
from lyra.sdk.client_contract import CLIENT_SCHEMA_VERSION, JSON_SCHEMA_DIALECT
from lyra.sdk.models.metric import (
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
from lyra_app.plugin_runtime import (
    SourceSnapshot,
    StartupSnapshot,
    config_fingerprint,
    copy_source,
    publish_snapshot,
    snapshot_path,
    source_hash,
)
from lyra_app.plugins import (
    MANIFEST_FILENAME,
    prepare_configured_repo,
    resolved_git_ref,
)

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


class CatalogUnavailableError(RuntimeError):
    """The startup catalog failed or has not been initialized."""


class MetricPayloadValidationError(Exception):
    """Report structured validation failures for a metric request payload."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        """Initialize the exception with API-compatible validation errors."""
        self.errors = errors
        super().__init__("metric payload validation failed")


TASK_REGISTRY: dict[str, MetricRegistryEntry] = {}


@dataclass
class _CatalogState:
    loaded: bool = False
    fingerprint: str | None = None
    error: str | None = None
    sources: list[SourceSnapshot] = field(default_factory=list)


_catalog = _CatalogState()


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


def _prepare_catalog(
    config: LyraConfig, temporary: Path
) -> tuple[dict[str, MetricRegistryEntry], StartupSnapshot]:
    manifests: list[tuple[CompiledPluginManifestV4, Path, str]] = []
    sources: list[SourceSnapshot] = []
    routing: dict[str, str] = {}
    destination = config.plugins.catalog_dir / "sources"
    captured = temporary / "captured"
    captured.mkdir()
    checkouts = temporary / "checkouts"
    checkouts.mkdir()
    for repo in config.plugins.repos:
        if not repo.enabled:
            continue
        synced = prepare_configured_repo(checkouts / repo.id, repo)
        revision = (
            resolved_git_ref(synced.path) if repo.source_kind != "directory" else None
        )
        target = captured / repo.id
        copy_source(synced.path, target)
        manifest = load_plugin_manifest(target)
        names = {metric.name for metric in manifest.metrics}
        unknown = set(repo.routing) - names
        if unknown:
            msg = (
                f"Repository {repo.id!r} has routing overrides for missing "
                f"metrics: {', '.join(sorted(unknown))}"
            )
            raise ValueError(msg)
        routing.update(
            {
                name: repo.routing.get(name, config.plugins.default_queue)
                for name in names
            }
        )
        manifests.append((manifest, target, repo.id))
        sources.append(
            SourceSnapshot(
                repo_id=repo.id,
                path=destination / repo.id,
                resolved_ref=revision,
                content_hash=source_hash(target),
            )
        )
    registry = _build_registry(manifests, routing)
    if destination.exists():
        shutil.rmtree(destination)
    captured.replace(destination)
    return registry, StartupSnapshot(
        status="ready",
        config_fingerprint=config_fingerprint(config),
        sources=sources,
        metric_queues=routing,
        catalog_fingerprint=_fingerprint_payload(
            _normalised_manifest_payload(manifests, routing)
        ),
    )


def initialize_catalog(config: LyraConfig | None = None) -> None:
    """Prepare and publish the catalog once during API startup.

    Plugin failures leave diagnostics available and the catalog unavailable.
    """
    config = get_config() if config is None else config
    reset_catalog()
    config.plugins.catalog_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(f"{snapshot_path(config)}.lock"):
        pending = StartupSnapshot(
            status="initializing", config_fingerprint=config_fingerprint(config)
        )
        publish_snapshot(config, pending)
        try:
            with tempfile.TemporaryDirectory(
                dir=config.plugins.catalog_dir, prefix=".startup-"
            ) as root:
                registry, snapshot = _prepare_catalog(config, Path(root))
            publish_snapshot(config, snapshot)
        except (
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
            _catalog.error = (
                f"Plugin initialization failed ({type(exc).__name__}); "
                "inspect server logs, correct the source or configuration, "
                "and restart."
            )
            logger.exception("Plugin catalog initialization failed")
            pending.status = "failed"
            publish_snapshot(config, pending)
            return
        TASK_REGISTRY.update(registry)
        _catalog.sources.extend(snapshot.sources)
        _catalog.fingerprint = snapshot.catalog_fingerprint
        _catalog.error = None
        _catalog.loaded = True


def catalog_error() -> str | None:
    """Return the sanitized initialization failure, if any.

    Returns:
        The failure message or ``None``.
    """
    return _catalog.error


def resolved_source_refs() -> dict[str, str | None]:
    """Expose resolved Git commits for the loaded source snapshots.

    Returns:
        Repository IDs mapped to captured Git revisions.
    """
    return {source.repo_id: source.resolved_ref for source in _catalog.sources}


def ensure_catalog_loaded() -> None:
    """Require a successful startup catalog without retrying initialization.

    Raises:
        CatalogUnavailableError: If catalog initialization has not succeeded.
    """
    if not _catalog.loaded:
        raise CatalogUnavailableError(
            _catalog.error or "Plugin catalog is not initialized."
        )


def get_catalog_fingerprint() -> str:
    """Return the internal fingerprint after ensuring the catalog is loaded."""
    ensure_catalog_loaded()
    return _catalog.fingerprint or _empty_catalog_fingerprint()


def get_loaded_catalog_fingerprint() -> str:
    """Return the current internal fingerprint without loading the catalog."""
    return _catalog.fingerprint or _empty_catalog_fingerprint()


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
    return _catalog.loaded


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


def reset_catalog() -> None:
    """Clear all loaded metrics and catalog initialization state."""
    TASK_REGISTRY.clear()
    _catalog.fingerprint = None
    _catalog.loaded = False
    _catalog.sources.clear()
    _catalog.error = None
