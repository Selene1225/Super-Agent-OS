"""Unified logging configuration for the application."""

import logging
import sys

from app.utils.config import get_settings


def setup_logging() -> logging.Logger:
    """Configure and return the application root logger."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Root logger for the app
    logger = logging.getLogger("sao")
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


# Module-level convenience — import `logger` from here directly.
logger = setup_logging()
