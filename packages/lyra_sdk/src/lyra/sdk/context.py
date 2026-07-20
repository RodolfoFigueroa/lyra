from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from lyra.sdk.db import LyraDB


class RunContext(Protocol):
    @property
    def job_id(self) -> str: ...

    @property
    def metric(self) -> str: ...

    @property
    def logger(self) -> logging.Logger: ...

    @property
    def temp_dir(self) -> Path: ...

    @property
    def db(self) -> LyraDB | None: ...

    def emit_event(self, event: str, data: dict[str, Any] | None = None) -> None: ...

    def check_cancelled(self) -> None: ...


__all__ = ["RunContext"]
