"""Connection and request options for Lyra API clients."""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Options that affect submission and waiting for a metric run."""

    timeout: float | None = None
    poll_interval: float = 5.0

    def __post_init__(self) -> None:
        """Reject invalid polling cadence before submitting a run.

        Raises:
            ValueError: If the polling interval is nonpositive or non-finite.
        """
        if not math.isfinite(self.poll_interval) or self.poll_interval <= 0:
            msg = "poll_interval must be positive and finite"
            raise ValueError(msg)
