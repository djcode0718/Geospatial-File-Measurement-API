"""Logging configuration for the application."""

import logging
import sys

from app.core.config import get_settings


def setup_logging() -> logging.Logger:
    """Configure structured console logging with timestamp and log levels."""
    settings = get_settings()
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    log_format = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Suppress overly chatty external libraries if needed
    logging.getLogger("uvicorn.access").setLevel(log_level)

    logger = logging.getLogger(settings.PROJECT_NAME)
    logger.setLevel(log_level)
    return logger
