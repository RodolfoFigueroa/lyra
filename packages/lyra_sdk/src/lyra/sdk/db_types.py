"""Type definitions for plugin database queries and results."""

from typing import NamedTuple


class Bounds(NamedTuple):
    """A spatial bounding box ordered as minimum x/y and maximum x/y."""

    xmin: float
    ymin: float
    xmax: float
    ymax: float


__all__ = ["Bounds"]
