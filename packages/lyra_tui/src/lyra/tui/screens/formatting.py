"""Formatting helpers shared by terminal-interface screens."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class Stringable(Protocol):
    """Value that provides a human-readable string representation."""

    def __str__(self) -> str:
        """Produce the value's display text."""
        ...


def bool_label(*, value: bool) -> str:
    """Format a boolean for compact tabular display.

    Returns:
        ``"yes"`` when true and ``"no"`` otherwise.
    """
    return "yes" if value else "no"


def count_label(value: int | None) -> str:
    """Format an optional count.

    Returns:
        The decimal count or ``"unknown"`` when unavailable.
    """
    return "unknown" if value is None else str(value)


def join_values(values: list[str]) -> str:
    """Join and bound a collection of display values.

    Returns:
        A comma-separated label, or ``"none"`` for an empty collection.
    """
    return truncate(", ".join(values) if values else "none")


def timestamp_label(value: datetime | None) -> str:
    """Format an optional timestamp in UTC.

    Returns:
        A seconds-precision ISO timestamp or ``"unknown"``.
    """
    if value is None:
        return "unknown"
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def truncate(value: Stringable, *, limit: int = 64) -> str:
    """Bound display text to a maximum character count.

    Returns:
        The original text or an ellipsis-terminated prefix.
    """
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."
