"""Metric registry construction and routing metadata management."""

import hashlib
import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from operator import itemgetter
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from lyra.sdk.client_contract import CLIENT_SCHEMA_VERSION, JSON_SCHEMA_DIALECT
from lyra.sdk.models.metric import (
    MetricCatalogResponse,
    MetricInfo,
    build_metric_search_text,
)
from lyra.sdk.models.plugin import (
    MetricManifest,
)
from lyra.sdk.types import JsonObject, JsonValue

from lyra_app.config import LyraConfig, get_config
from lyra_app.plugins import InstalledPlugin, load_installed_plugins

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricRegistryEntry:
    """Bundle a canonical metric with validation, routing, and catalog metadata."""

    metric: MetricManifest
    plugin_name: str
    plugin_version: str
    request_schema: JsonObject
    request_validator: Validator
    queue: str
    distribution: str
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


_catalog = _CatalogState()


def _empty_catalog_fingerprint() -> str:
    return _fingerprint_payload([])


def _fingerprint_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _normalised_manifest_payload(
    plugins: list[InstalledPlugin],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for plugin in plugins:
        data = plugin.manifest.model_dump(mode="json")
        data["distribution"] = plugin.distribution
        for metric in data["metrics"]:
            metric["queue"] = plugin.metric_queues[metric["name"]]
        data["metrics"] = sorted(data["metrics"], key=itemgetter("name"))
        payload.append(data)
    return sorted(payload, key=itemgetter("distribution"))


def _build_request_validator(metric_name: str, schema: JsonObject) -> Validator:
    validator_class = validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        msg = f"Metric {metric_name!r} has an invalid request_schema: {exc}"
        raise RuntimeError(msg) from exc
    return validator_class(schema)


def _build_registry(
    plugins: list[InstalledPlugin],
) -> dict[str, MetricRegistryEntry]:
    catalog_fingerprint = public_catalog_fingerprint(
        [
            _metric_info_from_manifest(metric)
            for plugin in plugins
            for metric in plugin.manifest.metrics
        ]
    )
    registry: dict[str, MetricRegistryEntry] = {}
    for plugin in plugins:
        manifest = plugin.manifest
        for metric in manifest.metrics:
            if metric.name in registry:
                msg = f"Duplicate metric name in plugin manifests: {metric.name!r}"
                raise RuntimeError(msg)
            try:
                queue = plugin.metric_queues[metric.name]
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
                distribution=plugin.distribution,
                catalog_fingerprint=catalog_fingerprint,
            )
    return registry


def initialize_catalog(config: LyraConfig | None = None) -> None:
    """Read installed manifests once, preserving diagnostics on startup failure."""
    config = get_config() if config is None else config
    reset_catalog()
    try:
        plugins = load_installed_plugins(config.plugins)
        registry = _build_registry(plugins)
    except (OSError, ValueError, RuntimeError) as exc:
        _catalog.error = (
            f"Plugin initialization failed ({type(exc).__name__}); "
            "inspect server logs, correct the installed plugins or configuration, "
            "and restart."
        )
        logger.exception("Plugin catalog initialization failed")
        return
    TASK_REGISTRY.update(registry)
    _catalog.fingerprint = _fingerprint_payload(_normalised_manifest_payload(plugins))
    _catalog.loaded = True


def catalog_error() -> str | None:
    """Return the sanitized initialization failure, if any.

    Returns:
        The failure message or ``None``.
    """
    return _catalog.error


def ensure_catalog_loaded() -> None:
    """Require a successful startup catalog without retrying initialization.

    Raises:
        CatalogUnavailableError: If catalog initialization has not succeeded.
    """
    if not _catalog.loaded:
        raise CatalogUnavailableError(
            _catalog.error or "Plugin catalog is not initialized."
        )


def get_loaded_catalog_fingerprint() -> str:
    """Return the current internal fingerprint without loading the catalog."""
    return _catalog.fingerprint or _empty_catalog_fingerprint()


def _public_metric_payload(metrics: list[MetricInfo]) -> list[dict[str, Any]]:
    return [
        metric.model_dump(mode="json")
        for metric in sorted(metrics, key=lambda item: item.name)
    ]


def public_catalog_fingerprint(metrics: list[MetricInfo]) -> str:
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


def get_metric_info(name: str) -> MetricInfo | None:
    """Return public metadata for a named metric when it exists."""
    entry = get_metric_entry(name)
    if entry is None:
        return None
    return _metric_info_from_entry(entry)


def get_metrics_info() -> list[MetricInfo]:
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
            request schema.
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
    return deepcopy(payload)


def _format_validation_error(error: JsonSchemaValidationError) -> dict[str, Any]:
    return {
        "loc": list(error.path),
        "msg": error.message,
        "type": str(error.validator),
    }


def _metric_info_from_entry(entry: MetricRegistryEntry) -> MetricInfo:
    return _metric_info_from_manifest(entry.metric)


def _metric_info_from_manifest(metric: MetricManifest) -> MetricInfo:
    return MetricInfo(
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
    _catalog.error = None
