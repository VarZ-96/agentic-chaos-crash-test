from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        record_ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        log_entry: dict[str, object] = {
            "timestamp": record_ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context is not None:
            log_entry["context"] = context

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def configure_logging(level: str = "INFO", stream: Any = None) -> None:
    """Configure structured JSON logging on the 'chaos' root logger.

    Idempotent: calling this multiple times installs exactly one handler.
    """
    logger = logging.getLogger("chaos")
    numeric_level = getattr(logging, level.upper(), logging.INFO) if isinstance(level, str) else level
    logger.setLevel(numeric_level)
    logger.propagate = False

    target_stream = sys.stdout if stream is None else stream

    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and isinstance(handler.formatter, JSONFormatter):
            handler.setLevel(numeric_level)
            if stream is not None and handler.stream != stream:
                handler.setStream(stream)
            return

    logger.handlers.clear()
    handler = logging.StreamHandler(target_stream)
    handler.setLevel(numeric_level)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger rooted under 'chaos'."""
    if name == "chaos" or name.startswith("chaos."):
        return logging.getLogger(name)
    return logging.getLogger(f"chaos.{name}")
