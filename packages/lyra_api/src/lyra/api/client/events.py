"""Shared SSE parsing and resumable job-stream policy."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lyra.api.exceptions import (
    DownloadError,
    JobEventCursorGapError,
    JobEventStreamError,
    JobWaitTimeoutError,
)
from lyra.sdk.models.job import JobEventRecord, JobLifecycleEvent

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class SSEBuffer:
    """Accumulate SSE fields and validate complete job event records."""

    data_lines: list[str] = field(default_factory=list)
    event_id: str | None = None

    def flush(self) -> JobEventRecord | None:
        """Dispatch a buffered event, including at EOF.

        Returns:
            A complete event, or None if no data was buffered.

        Raises:
            DownloadError: If the event lacks an ID or has invalid JSON/payload.
        """
        if not self.data_lines:
            self.event_id = None
            return None
        if not self.event_id:
            err = "Job event did not include an SSE id."
            raise DownloadError(err)
        try:
            record = JobEventRecord(
                id=self.event_id, event=json.loads("\n".join(self.data_lines))
            )
        except ValueError as exc:
            err = "Invalid job event JSON or payload."
            raise DownloadError(err) from exc
        self.data_lines.clear()
        self.event_id = None
        return record

    def add(self, line: str | bytes) -> JobEventRecord | None:
        """Consume a line, dispatching an event on blank lines.

        Returns:
            A complete event, or None for incomplete events and comments.

        Raises:
            DownloadError: If a line is not valid UTF-8.
        """
        try:
            decoded = line.decode() if isinstance(line, bytes) else line
        except UnicodeError as exc:
            err = "Invalid job event encoding."
            raise DownloadError(err) from exc
        decoded = decoded.rstrip("\r\n")
        if not decoded:
            return self.flush()
        if decoded.startswith(":"):
            return None
        name, _, value = decoded.partition(":")
        value = value.removeprefix(" ")
        if name == "data":
            self.data_lines.append(value)
        elif name == "id":
            self.event_id = value
        return None


def terminal_event(record: JobEventRecord) -> bool:
    """Return whether the event signals a terminal job lifecycle state."""
    event = record.event
    return isinstance(event, JobLifecycleEvent) and event.status in {
        "succeeded",
        "failed",
        "cancelled",
    }


class RetryableEventError(Exception):
    """An HTTP 5xx that should reconnect using the stream retry policy."""


@dataclass
class StreamState:
    """Track one stream's cursor, filtering, retries, and total deadline."""

    job_id: str
    cursor: str | None = None
    kinds: set[str] | None = None
    timeout: float | None = None
    max_reconnect_attempts: int = 5
    clock: Callable[[], float] = field(default_factory=lambda: time.monotonic)
    jitter: random.Random = field(default_factory=random.SystemRandom)
    attempts: int = field(default=0, init=False)
    terminal: bool = field(default=False, init=False)
    deadline: float | None = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the deadline and validate the reconnect limit.

        Raises:
            ValueError: If the reconnect limit is negative.
        """
        if self.max_reconnect_attempts < 0:
            err = "max_reconnect_attempts must be non-negative"
            raise ValueError(err)
        self.deadline = None if self.timeout is None else self.clock() + self.timeout

    def check_deadline(self) -> None:
        """Check the total deadline, including while heartbeats arrive.

        Raises:
            JobWaitTimeoutError: If the total deadline has expired.
        """
        if self.deadline is not None and self.clock() >= self.deadline:
            err = f"Timed out waiting for events from job {self.job_id}."
            raise JobWaitTimeoutError(
                err,
                job_id=self.job_id,
                last_event_id=self.cursor,
                attempts=self.attempts,
            )

    def headers(self, defaults: dict[str, str]) -> dict[str, str]:
        """Return request headers with the latest accepted event cursor."""
        headers = dict(defaults)
        if self.cursor is not None:
            headers["Last-Event-ID"] = self.cursor
        return headers

    def read_timeout(self, default: float) -> float:
        """Return a read timeout bounded by the remaining total deadline."""
        if self.deadline is None:
            return default
        return max(0.001, min(default, self.deadline - self.clock()))

    def accept(self, record: JobEventRecord) -> bool:
        """Advance state for new events.

        Returns:
            Whether the caller should yield the event.
        """
        self.check_deadline()
        self.terminal = self.terminal or terminal_event(record)
        if record.id == self.cursor:
            return False
        self.cursor = record.id
        self.attempts = 0
        return self.kinds is None or record.event.kind in self.kinds

    def validate_status(self, status: int) -> None:
        """Validate stream status before reading its lines.

        Raises:
            JobEventCursorGapError: If retained history cannot serve the cursor.
            RetryableEventError: If HTTP 5xx warrants reconnection.
            DownloadError: For other unsuccessful statuses.
        """
        if status == 409:
            err = (
                f"Event history for job {self.job_id} no longer contains {self.cursor}."
            )
            raise JobEventCursorGapError(
                err,
                job_id=self.job_id,
                last_event_id=self.cursor,
                attempts=self.attempts,
            )
        if 500 <= status < 600:
            err = f"Failed to stream job events. HTTP {status}"
            raise RetryableEventError(err)
        if status != 200:
            err = f"Failed to stream job events. HTTP {status}"
            raise DownloadError(err)

    def retry_delay(self) -> float:
        """Count a retry and return deadline-bounded exponential full jitter.

        Returns:
            Seconds to wait before reconnecting.

        Raises:
            JobEventStreamError: If consecutive reconnect attempts are exhausted.
        """
        self.check_deadline()
        self.attempts += 1
        if self.attempts > self.max_reconnect_attempts:
            err = f"Could not resume the event stream for job {self.job_id}."
            raise JobEventStreamError(
                err,
                job_id=self.job_id,
                last_event_id=self.cursor,
                attempts=self.attempts,
            )
        cap = min(8.0, 0.5 * (2 ** min(self.attempts - 1, 4)))
        delay = self.jitter.uniform(0, cap)
        if self.deadline is None:
            return delay
        return min(delay, max(0.0, self.deadline - self.clock()))
