import logging
import os


DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _build_log_handler() -> logging.Handler:
    log_file_path = os.environ.get("LYRA_LOG_FILE")

    if not log_file_path:
        return logging.StreamHandler()

    log_dir = os.path.dirname(log_file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

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