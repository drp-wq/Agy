"""Structured JSON logging with rotation and automatic secret redaction."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.security import SecretRedactionFilter

_REDACTION_FILTER = SecretRedactionFilter()


def register_sensitive_value(value: str | None) -> None:
    """Register a secret string so it is scrubbed from all log output."""
    _REDACTION_FILTER.register_secret(value)


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON for auditability."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = _REDACTION_FILTER.redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    """Configure root logger with rotating JSON file handler and console handler."""
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("multi_exchange")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.propagate = False

    formatter = JsonFormatter()

    file_handler = RotatingFileHandler(
        log_dir / "engine.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_REDACTION_FILTER)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(_REDACTION_FILTER)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    return root
