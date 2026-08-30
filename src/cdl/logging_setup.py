"""Logging: console plus logs/cdl.log.

Never log a credential or the endpoint URL. `mask_library` keeps the schema/library
name out of every message that carries SQL.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import Settings, project_root

LOGGER_NAME = "cdl"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LIBRARY_PLACEHOLDER = "<LIBRARY>"

_configured = False


def log_path() -> Path:
    return project_root() / "logs" / "cdl.log"


def get_logger(name: str | None = None) -> logging.Logger:
    """Child logger under the single `cdl` logger."""
    if name is None or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def setup_logging(
    level: int = logging.INFO,
    *,
    console: bool = True,
    console_level: int | None = None,
) -> logging.Logger:
    """Attach one console handler and one rotating file handler, once.

    The file gets everything at `level`; the console can be quieter (`console_level`)
    so log lines do not bury the decision the trader is reading.
    """
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    if _configured:
        return logger

    formatter = logging.Formatter(LOG_FORMAT)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                           encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # A read-only or unavailable logs directory must not stop a limit check.
        pass

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(console_level if console_level is not None else level)
        logger.addHandler(console_handler)

    logger.propagate = False
    _configured = True
    return logger


def mask_library(text: str, library: str) -> str:
    """Replace the configured library name with a placeholder."""
    if not library:
        return str(text)
    return str(text).replace(library, LIBRARY_PLACEHOLDER)


def log_startup(settings: Settings) -> None:
    """Log the config path and every table's effective source."""
    logger = get_logger("startup")
    logger.info("config: %s", settings.config_path or "(defaults, no config.ini found)")
    if settings.overrides:
        logger.info("environment overrides: %s", ", ".join(settings.overrides))
    for table, source in settings.source_summary().items():
        logger.info("source: %-8s -> %s", table, source)
    logger.info("ffr weight column: %s", settings.ffr.weight_column)
    logger.info("store db_path: %s", settings.store.db_path)
