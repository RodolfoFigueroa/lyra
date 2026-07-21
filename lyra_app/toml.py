from __future__ import annotations

import tomllib
from datetime import date, datetime
from typing import BinaryIO, TypeAlias, cast

TomlValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | date
    | datetime
    | list["TomlValue"]
    | dict[str, "TomlValue"]
)
TomlTable: TypeAlias = dict[str, TomlValue]


class TomlNormalizationError(ValueError):
    """Raised when normalized TOML keys or values are ambiguous or empty."""


def _normalized_string(value: str, *, location: str) -> str:
    normalized = value.strip()
    if not normalized:
        msg = f"{location} must be a non-empty string"
        raise TomlNormalizationError(msg)
    return normalized


def _normalize_value(value: TomlValue, *, location: str) -> TomlValue:
    if isinstance(value, str):
        return _normalized_string(value, location=location)
    if isinstance(value, list):
        return [
            _normalize_value(item, location=f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return normalize_toml_table(value, location=location)
    return value


def normalize_toml_table(
    table: TomlTable,
    *,
    location: str = "TOML document",
) -> TomlTable:
    """Trim TOML keys and strings while preserving its recursive value types."""
    normalized: TomlTable = {}
    for raw_key, raw_value in table.items():
        key = _normalized_string(raw_key, location=f"{location} key")
        if key in normalized:
            msg = f"{location} contains duplicate key after trimming: {key!r}"
            raise TomlNormalizationError(msg)
        normalized[key] = _normalize_value(
            raw_value,
            location=f"{location}.{key}",
        )
    return normalized


def load_normalized_toml(source: BinaryIO) -> TomlTable:
    """Parse and normalize one TOML document at the untyped library boundary."""
    parsed = cast("TomlTable", tomllib.load(source))
    return normalize_toml_table(parsed)


def loads_normalized_toml(source: str) -> TomlTable:
    """Parse and normalize TOML text at the untyped library boundary."""
    parsed = cast("TomlTable", tomllib.loads(source))
    return normalize_toml_table(parsed)


__all__ = [
    "TomlNormalizationError",
    "TomlTable",
    "TomlValue",
    "load_normalized_toml",
    "loads_normalized_toml",
    "normalize_toml_table",
]
