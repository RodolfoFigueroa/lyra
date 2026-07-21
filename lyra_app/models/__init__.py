"""Internal data models used by the Lyra application."""

from lyra.sdk.models.spatial import BoundsReference as ExplicitBoundsUnion
from lyra.sdk.models.spatial import LocationReference as ExplicitLocationUnion

__all__ = [
    "ExplicitBoundsUnion",
    "ExplicitLocationUnion",
]
