from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class Stringable(Protocol):
    def __str__(self) -> str: ...


def bool_label(*, value: bool) -> str:
    return "yes" if value else "no"


def count_label(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def join_values(values: list[str]) -> str:
    return truncate(", ".join(values) if values else "none")


def timestamp_label(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def truncate(value: Stringable, *, limit: int = 64) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."
