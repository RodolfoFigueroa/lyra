"""Per-wait event loop for cancellable synchronous polling observations."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Self, TypeVar

from lyra.api.exceptions import DownloadError

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from types import TracebackType
    from typing import Any

_ResponseT = TypeVar("_ResponseT")


class PollingBridge:
    """Own one private loop thread, starting it lazily on the first observation."""

    def __init__(self) -> None:
        """Initialize without starting background work."""
        self._loop: asyncio.AbstractEventLoop | None = None

    def __enter__(self) -> Self:
        """Return the bridge for this wait."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop observations and let the thread finish HTTP cleanup asynchronously."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    @staticmethod
    def _run(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    def observe(
        self, observation: Coroutine[Any, Any, _ResponseT], *, request_timeout: float
    ) -> _ResponseT:
        """Bound an observation, including startup, without blocking on cleanup.

        Returns:
            The validated endpoint response.

        Raises:
            DownloadError: If this request times out before the job deadline.
        """
        deadline = time.monotonic() + request_timeout
        try:
            if self._loop is None:
                self._loop = self._start()
            future = asyncio.run_coroutine_threadsafe(observation, self._loop)
        except BaseException:
            observation.close()
            raise
        try:
            return future.result(timeout=max(0.0, deadline - time.monotonic()))
        except TimeoutError as exc:
            msg = "Failed to observe job: request timed out"
            raise DownloadError(msg, retryable=True) from exc
        finally:
            future.cancel()

    def _start(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.new_event_loop()
        try:
            threading.Thread(
                target=self._run, args=(loop,), name="lyra-polling", daemon=True
            ).start()
        except BaseException:
            loop.close()
            raise
        return loop
