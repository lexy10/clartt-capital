"""Structured JSON logging configuration for the Execution Engine."""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON for centralized log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "execution-engine",
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "account_id"):
            log_entry["account_id"] = record.account_id  # type: ignore[attr-defined]
        if hasattr(record, "signal_id"):
            log_entry["signal_id"] = record.signal_id  # type: ignore[attr-defined]
        return json.dumps(log_entry)


def configure_logging() -> None:
    """Configure structured JSON logging for the execution engine."""
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Remove existing handlers
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)
