import logging
import os
from pathlib import Path

from lyra_app.constants import DEFAULT_LOG_FORMAT, DEFAULT_LOG_LEVEL


def _build_log_handler() -> logging.Handler:
    log_file_var = os.environ.get("LYRA_LOG_FILE")

    if log_file_var is None:
        return logging.StreamHandler()

    log_file_path = Path(log_file_var)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    return logging.FileHandler(log_file_path, encoding="utf-8")


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("lyra")
    logger.setLevel(os.environ.get("LYRA_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper())

    if logger.handlers:
        return logger

    handler = _build_log_handler()
    handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
