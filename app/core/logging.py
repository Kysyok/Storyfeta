"""Structured JSON logging to stdout. No log files (12-factor).

Fields bound with :func:`log_context` / :func:`bind_log_context` (``request_id``,
``room``, ``player_id``...) are added to every record logged in that async
context, so all lines of one request or WebSocket connection can be joined.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})  # noqa: B039 - never mutated


def current_log_context() -> dict[str, Any]:
    """The fields currently bound for this async context."""
    return dict(_context.get())


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Bind ``fields`` for the duration of the block (restored afterwards)."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def bind_log_context(**fields: Any) -> None:
    """Add ``fields`` to the current context, e.g. once a handler learns the room.

    Lasts until the enclosing :func:`log_context` (the request middleware) exits.
    """
    _context.set({**_context.get(), **fields})


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_context.get())
        # Anything passed via ``extra={...}`` becomes a top-level field (and wins).
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        )
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Send every logger (including uvicorn's) through one JSON stdout handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
