import json
from pathlib import Path

from lyra.sdk.models.plugin import PluginManifest
from pydantic import ValidationError as PydanticValidationError

from lyra_app.plugins import MANIFEST_FILENAME


def load_legacy_plugin_manifest(path: Path) -> PluginManifest:
    manifest_path = path / MANIFEST_FILENAME
    if not manifest_path.exists():
        msg = f"Plugin repo {path} is missing required {MANIFEST_FILENAME}."
        raise RuntimeError(msg)

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        return PluginManifest.model_validate(raw)
    except json.JSONDecodeError as exc:
        msg = f"Plugin manifest {manifest_path} is not valid JSON."
        raise RuntimeError(msg) from exc
    except PydanticValidationError as exc:
        msg = f"Plugin manifest {manifest_path} is invalid: {exc}"
        raise RuntimeError(msg) from exc


__all__ = ["load_legacy_plugin_manifest"]
