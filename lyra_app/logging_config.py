import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from lyra_app.config import LyraConfig, get_config


class JsonLineFormatter(logging.Formatter):
    """Render application records as one machine-readable JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "structured_fields", None)
        if isinstance(fields, dict):
            payload["fields"] = fields
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def _build_log_handler(log_file: Path | None) -> logging.Handler:
    if log_file is None:
        return logging.StreamHandler()

    log_file.parent.mkdir(parents=True, exist_ok=True)

    return logging.FileHandler(log_file, encoding="utf-8")


def _logging_config(config: LyraConfig | None) -> tuple[str, Path | None]:
    if config is None:
        config = get_config()
    return config.logging.level, config.logging.file


def configure_logging(config: LyraConfig | None = None) -> logging.Logger:
    logger = logging.getLogger("lyra_app")
    level, log_file = _logging_config(config)
    logger.setLevel(level.upper())

    if logger.handlers:
        return logger

    handler = _build_log_handler(log_file)
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


__all__ = ["JsonLineFormatter", "configure_logging"]
