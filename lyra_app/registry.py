import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from lyra.sdk.models.metric import MetricInfoV2
from lyra.sdk.models.plugin_v2 import MetricManifestV2, PluginManifestV2
from pydantic import ValidationError as PydanticValidationError

from lyra_app.plugins import MANIFEST_FILENAME, sync_catalog_repos

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricRegistryEntry:
    metric: MetricManifestV2
    plugin_name: str
    plugin_version: str
    request_validator: Any
    queue: str
    entrypoint: str


@dataclass(frozen=True)
class CatalogRefreshResult:
    updated_plugins: list[str]
    previous_catalog_fingerprint: str | None
    catalog_fingerprint: str
    catalog_changed: bool


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
    manifests: list[tuple[PluginManifestV2, Path]],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for manifest, _path in manifests:
        data = manifest.model_dump(mode="json")
        data["metrics"] = sorted(data["metrics"], key=lambda item: item["name"])
        payload.append(data)
    return sorted(
        payload,
        key=lambda item: (item["plugin"]["name"], item["plugin"]["version"]),
    )


def load_plugin_manifest(path: Path) -> PluginManifestV2:
    manifest_path = path / MANIFEST_FILENAME
    if not manifest_path.exists():
        msg = f"Plugin repo {path} is missing required {MANIFEST_FILENAME}."
        raise RuntimeError(msg)

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        return PluginManifestV2.model_validate(raw)
    except json.JSONDecodeError as exc:
        msg = f"Plugin manifest {manifest_path} is not valid JSON."
        raise RuntimeError(msg) from exc
    except PydanticValidationError as exc:
        msg = f"Plugin manifest {manifest_path} is invalid: {exc}"
        raise RuntimeError(msg) from exc


def _build_request_validator(metric: MetricManifestV2) -> Any:
    schema = metric.request_schema
    validator_class = validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        msg = f"Metric {metric.name!r} has an invalid request_schema: {exc}"
        raise RuntimeError(msg) from exc
    return validator_class(schema)


def _build_registry(
    manifests: list[tuple[PluginManifestV2, Path]],
) -> dict[str, MetricRegistryEntry]:
    registry: dict[str, MetricRegistryEntry] = {}
    for manifest, _path in manifests:
        for metric in manifest.metrics:
            if metric.name in registry:
                msg = f"Duplicate metric name in plugin manifests: {metric.name!r}"
                raise RuntimeError(msg)
            registry[metric.name] = MetricRegistryEntry(
                metric=metric,
                plugin_name=manifest.plugin.name,
                plugin_version=manifest.plugin.version,
                request_validator=_build_request_validator(metric),
                queue=metric.execution.queue,
                entrypoint=metric.entrypoint,
            )
    return registry


def refresh_catalog() -> CatalogRefreshResult:
    global _CATALOG_FINGERPRINT, _CATALOG_LOADED  # noqa: PLW0603

    previous_fingerprint = _CATALOG_FINGERPRINT
    synced = sync_catalog_repos()
    manifests = [(load_plugin_manifest(repo.path), repo.path) for repo in synced]
    registry = _build_registry(manifests)

    fingerprint = _fingerprint_payload(_normalised_manifest_payload(manifests))
    TASK_REGISTRY.clear()
    TASK_REGISTRY.update(registry)
    _CATALOG_FINGERPRINT = fingerprint
    _CATALOG_LOADED = True

    updated = [repo.entry.display_name for repo in synced if repo.changed]
    catalog_changed = previous_fingerprint != fingerprint
    logger.info(
        "Loaded %d metric manifest(s); catalog fingerprint=%s; changed=%s",
        len(TASK_REGISTRY),
        fingerprint,
        catalog_changed,
    )
    return CatalogRefreshResult(
        updated_plugins=updated,
        previous_catalog_fingerprint=previous_fingerprint,
        catalog_fingerprint=fingerprint,
        catalog_changed=catalog_changed,
    )


def ensure_catalog_loaded() -> None:
    if not _CATALOG_LOADED:
        refresh_catalog()


def get_catalog_fingerprint() -> str:
    ensure_catalog_loaded()
    return _CATALOG_FINGERPRINT or _empty_catalog_fingerprint()


def get_metric_entry(name: str) -> MetricRegistryEntry | None:
    ensure_catalog_loaded()
    return TASK_REGISTRY.get(name)


def get_metric_info(name: str) -> MetricInfoV2 | None:
    entry = get_metric_entry(name)
    if entry is None:
        return None
    return _metric_info_from_manifest(entry.metric)


def get_metrics_info() -> list[MetricInfoV2]:
    ensure_catalog_loaded()
    return [
        _metric_info_from_manifest(entry.metric) for entry in TASK_REGISTRY.values()
    ]


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
    return payload


def _format_validation_error(error: JsonSchemaValidationError) -> dict[str, Any]:
    return {
        "loc": list(error.path),
        "msg": error.message,
        "type": str(error.validator),
    }


def _metric_info_from_manifest(metric: MetricManifestV2) -> MetricInfoV2:
    return MetricInfoV2(
        name=metric.name,
        description=metric.description.strip(),
        request_schema=metric.request_schema,
        result_schema=metric.result_schema,
    )


def reload_tasks() -> None:
    refresh_catalog()


def reset_catalog() -> None:
    global _CATALOG_FINGERPRINT, _CATALOG_LOADED  # noqa: PLW0603

    TASK_REGISTRY.clear()
    _CATALOG_FINGERPRINT = None
    _CATALOG_LOADED = False
