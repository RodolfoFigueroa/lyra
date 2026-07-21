from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from lyra.sdk.db import LyraDB
    from lyra.sdk.models import JobMessageLevel


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
    def db(self) -> LyraDB: ...

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None: ...

    def report_message(
        self,
        message: str,
        *,
        level: JobMessageLevel = "info",
        fields: dict[str, Any] | None = None,
    ) -> None: ...

    def check_cancelled(self) -> None: ...


__all__ = ["RunContext"]
