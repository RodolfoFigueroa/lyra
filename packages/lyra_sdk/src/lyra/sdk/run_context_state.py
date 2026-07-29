"""Shared implementation details for run-context event semantics."""

from dataclasses import dataclass, field

from lyra.sdk.models import JobProgressEvent


@dataclass
class RunProgressState:
    """Validate and retain progress transitions accepted by a run context."""

    _last_event: JobProgressEvent | None = field(default=None, init=False, repr=False)

    @property
    def last_event(self) -> JobProgressEvent | None:
        """The most recently accepted progress event, if any."""
        return self._last_event

    def accept(self, event: JobProgressEvent) -> None:
        """Validate and retain one already-validated progress event.

        Raises:
            ValueError: If progress decreases or changes a stable field within
                the current stage.
        """
        previous = self._last_event
        if previous is not None and previous.stage == event.stage:
            if event.current < previous.current:
                msg = f"Progress for stage {event.stage!r} must not decrease."
                raise ValueError(msg)
            if previous.total is not None and event.total != previous.total:
                msg = f"Progress total for stage {event.stage!r} must remain stable."
                raise ValueError(msg)
            if previous.unit != event.unit:
                msg = f"Progress unit for stage {event.stage!r} must remain stable."
                raise ValueError(msg)
        self._last_event = event


__all__ = ["RunProgressState"]
