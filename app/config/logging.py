"""Structured, rotating application logging setup."""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Any


class SecretRedactionFilter(logging.Filter):
    """Remove common credential assignments from formatted log records."""

    _sensitive_keys = ("password", "secret", "token", "authorization")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for key in self._sensitive_keys:
            lowered = message.lower()
            marker = f"{key}="
            while marker in lowered:
                start = lowered.index(marker) + len(marker)
                end_candidates = [
                    index
                    for separator in (" ", "&", ",", ";")
                    if (index := lowered.find(separator, start)) != -1
                ]
                end = min(end_candidates, default=len(message))
                message = message[:start] + "***" + message[end:]
                lowered = message.lower()
        record.msg = message
        record.args = ()
        return True


def configure_logging(log_directory: Path, debug: bool = False) -> None:
    """Configure console and per-subsystem rotating file logs."""

    log_directory.mkdir(parents=True, exist_ok=True)
    level = "DEBUG" if debug else "INFO"
    common_handler: dict[str, Any] = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "detailed",
        "filters": ["redact"],
        "maxBytes": 5_000_000,
        "backupCount": 7,
        "encoding": "utf-8",
    }
    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["redact"],
            "level": level,
        },
        "application": {
            **common_handler,
            "filename": log_directory / "application.log",
            "level": level,
        },
        "errors": {**common_handler, "filename": log_directory / "errors.log", "level": "ERROR"},
        "email": {**common_handler, "filename": log_directory / "email.log", "level": level},
        "database": {**common_handler, "filename": log_directory / "database.log", "level": level},
    }
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"redact": {"()": SecretRedactionFilter}},
            "formatters": {
                "standard": {"format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s"},
                "detailed": {
                    "format": (
                        "%(asctime)s | %(levelname)s | %(name)s | %(process)d | "
                        "%(threadName)s | %(message)s"
                    )
                },
            },
            "handlers": handlers,
            "root": {"handlers": ["console", "application", "errors"], "level": level},
            "loggers": {
                "app.email": {"handlers": ["email"], "level": level, "propagate": True},
                "sqlalchemy.engine": {
                    "handlers": ["database"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        }
    )
