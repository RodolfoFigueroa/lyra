from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from lyra.sdk.db import LyraDB


class RunContext(Protocol):
    job_id: str
    metric: str
    logger: logging.Logger
    temp_dir: Path
    db: LyraDB | None

    def emit_event(self, event: str, data: dict[str, Any] | None = None) -> None: ...

    def check_cancelled(self) -> None: ...


__all__ = ["RunContext"]
