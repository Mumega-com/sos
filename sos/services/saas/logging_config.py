"""Structured JSON logging for SaaS services."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Format log records as structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Convert a log record to JSON."""
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[0]:
            log_entry["error"] = self.formatException(record.exc_info)

        # Add extra fields from the record
        for key in ("tenant", "tool", "duration_ms", "status", "event", "user_id", "action"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry)


def setup_logging(service_name: str = "saas", level: str = "INFO") -> None:
    """Configure JSON logging for a service.

    Args:
        service_name: Name of the service (e.g., 'saas', 'squad')
        level: Logging level (e.g., 'INFO', 'DEBUG', 'WARNING')
    """
    # Create formatter
    formatter = JSONFormatter()

    # Configure stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    # Set up root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Clear any existing handlers
    root.handlers = []

    # Add stdout handler
    root.addHandler(stdout_handler)

    # Configure file handler
    log_dir = Path.home() / ".sos" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_dir / f"{service_name}.jsonl")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
