import unicodedata
from typing import Literal

from lyra.sdk.client_contract import CLIENT_SCHEMA_VERSION, JSON_SCHEMA_DIALECT
from lyra.sdk.models.plugin_v4 import (
    FileOutputV4,
    OutputSpecV4,
    SpatialInputKindV4,
    TableOutputV4,
)
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.types import JsonObject, JsonValue
from pydantic import Field


def _append_search_part(parts: list[str], value: JsonValue) -> None:
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


class MetricInfoV4(StrictBaseModel):
    """Catalog metadata for one schema v4 metric exposed by the API."""

    name: str = Field(description="Public metric name.")
    description: str = Field(description="Human-readable metric description.")
    request_schema: JsonObject = Field(
        description="Effective JSON Schema for the client request payload.",
    )
    spatial_inputs: dict[str, SpatialInputKindV4] = Field(
        default_factory=dict,
        description=(
            "Request field names mapped to Lyra-owned spatial input kinds resolved "
            "before worker execution."
        ),
    )
    output: OutputSpecV4 = Field(
        description="Successful metric output declaration.",
    )

    def search_text(self) -> str:
        """Return derived lexical text for catalog search."""

        return build_metric_search_text(self)


def build_metric_search_text(metric: MetricInfoV4) -> str:
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
    if isinstance(output, TableOutputV4):
        for column in output.columns:
            _append_search_part(parts, column.name)
            _append_search_part(parts, column.description)
            _append_search_part(parts, column.unit)
        for column in output.batched_columns:
            _append_search_part(parts, column.source)
            _append_search_part(parts, column.name)
            _append_search_part(parts, column.description)
            _append_search_part(parts, column.unit)
    elif isinstance(output, FileOutputV4):
        _append_search_part(parts, output.media_type)
        for extension in output.extensions:
            _append_search_part(parts, extension)

    unique_parts = dict.fromkeys(parts)
    return " ".join(unique_parts)


class MetricCatalogResponse(StrictBaseModel):
    """Public metric catalog with a contract-only fingerprint."""

    client_schema_version: Literal[1] = Field(
        description="Version of the generated-client catalog contract.",
    )
    json_schema_dialect: Literal["https://json-schema.org/draft/2020-12/schema"] = (
        Field(
            description="JSON Schema dialect used by every metric request schema.",
        )
    )

    catalog_fingerprint: str = Field(
        min_length=1,
        description="SHA-256 fingerprint of the public metric catalog contract.",
    )
    metrics: list[MetricInfoV4] = Field(
        description="Client-facing metric metadata sorted by metric name.",
    )


__all__ = [
    "CLIENT_SCHEMA_VERSION",
    "JSON_SCHEMA_DIALECT",
    "MetricCatalogResponse",
    "MetricInfoV4",
    "build_metric_search_text",
    "normalize_metric_search_tokens",
]
