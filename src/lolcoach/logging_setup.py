"""Structured JSON logging.

Configured exactly once, from the application entrypoint. Domain modules only
ever create a module-level logger (``logger = logging.getLogger(__name__)``);
they must never call ``logging.basicConfig``.

Every emitted line is a single JSON object::

    {"timestamp": "...", "level": "INFO", "service": "lol-coach",
     "logger": "lolcoach.engine.scoring", "message": "...", "data": {...}}

Pass structured fields with ``logger.info("msg", extra={"data": {...}})``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Render a log record as one line of JSON."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": _utc_iso(),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            payload["data"] = data
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def configure_logging(service: str, level: str = "DEBUG") -> None:
    """Install the JSON handler on the root logger. Idempotent."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
