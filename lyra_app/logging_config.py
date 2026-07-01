import logging
from pathlib import Path

from lyra_app.config import LyraConfig, get_config
from lyra_app.constants import DEFAULT_LOG_FORMAT


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
    handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
