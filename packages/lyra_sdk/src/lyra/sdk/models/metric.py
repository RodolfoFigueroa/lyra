import unicodedata
from typing import Any

from lyra.sdk.models.plugin_v3 import (
    FileOutputV3,
    OutputSpecV3,
    SpatialInputKindV3,
    TableOutputV3,
)
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


def _append_search_part(parts: list[str], value: Any) -> None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            parts.append(stripped)


def normalize_metric_search_tokens(value: str) -> tuple[str, ...]:
    """Return stable, accent-insensitive tokens for lexical metric search.

    Identifier boundaries are treated like whitespace, including snake_case,
    kebab-case, and camelCase boundaries. Repeated tokens are removed while
    preserving their first-seen order so callers cannot change ranking by
    repeating a search term.
    """

    segments: list[str] = []
    current: list[str] = []

    def finish_segment() -> None:
        if current:
            segments.append("".join(current))
            current.clear()

    for index, character in enumerate(value):
        if not character.isalnum():
            finish_segment()
            continue

        previous = current[-1] if current else None
        following = value[index + 1] if index + 1 < len(value) else None
        starts_camel_word = (
            character.isupper()
            and previous is not None
            and (
                previous.islower()
                or previous.isdigit()
                or (
                    previous.isupper() and following is not None and following.islower()
                )
            )
        )
        if starts_camel_word:
            finish_segment()
        current.append(character)
    finish_segment()

    tokens: list[str] = []
    for segment in segments:
        normalized = unicodedata.normalize("NFKD", segment).casefold()
        token = "".join(
            character
            for character in normalized
            if character.isalnum() and not unicodedata.combining(character)
        )
        if token:
            tokens.append(token)
    return tuple(dict.fromkeys(tokens))


class MetricInfoV3(StrictBaseModel):
    """Catalog metadata for one schema v3 metric exposed by the API."""

    name: str = Field(description="Public metric name.")
    description: str = Field(description="Human-readable metric description.")
    request_schema: dict[str, Any] = Field(
        description="Effective JSON Schema for the client request payload.",
    )
    spatial_inputs: dict[str, SpatialInputKindV3] = Field(
        default_factory=dict,
        description=(
            "Request field names mapped to Lyra-owned spatial input kinds resolved "
            "before worker execution."
        ),
    )
    output: OutputSpecV3 = Field(
        description="Successful metric output declaration.",
    )

    def search_text(self) -> str:
        """Return derived lexical text for catalog search."""

        return build_metric_search_text(self)


def build_metric_search_text(metric: MetricInfoV3) -> str:
    """Build deterministic lexical text from public metric catalog fields."""

    parts: list[str] = [metric.name, metric.description]
    properties = metric.request_schema.get("properties")
    if isinstance(properties, dict):
        for field_name, property_schema in properties.items():
            _append_search_part(parts, field_name)
            if isinstance(property_schema, dict):
                _append_search_part(parts, property_schema.get("description"))

    output = metric.output
    _append_search_part(parts, output.kind)
    if isinstance(output, TableOutputV3):
        for column in output.columns:
            _append_search_part(parts, column.name)
            _append_search_part(parts, column.description)
            _append_search_part(parts, column.unit)
        for column in output.batched_columns:
            _append_search_part(parts, column.source)
            _append_search_part(parts, column.name)
            _append_search_part(parts, column.description)
            _append_search_part(parts, column.unit)
    elif isinstance(output, FileOutputV3):
        _append_search_part(parts, output.media_type)
        for extension in output.extensions:
            _append_search_part(parts, extension)

    unique_parts = dict.fromkeys(parts)
    return " ".join(unique_parts)


class MetricCatalogResponse(StrictBaseModel):
    """Public metric catalog with a contract-only fingerprint."""

    catalog_fingerprint: str = Field(
        min_length=1,
        description="SHA-256 fingerprint of the public metric catalog contract.",
    )
    metrics: list[MetricInfoV3] = Field(
        description="Client-facing metric metadata sorted by metric name.",
    )


__all__ = [
    "MetricCatalogResponse",
    "MetricInfoV3",
    "build_metric_search_text",
    "normalize_metric_search_tokens",
]
