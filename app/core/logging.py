"""Structured JSON logging to stdout. No log files (12-factor).

Every line has ``timestamp``, ``level``, ``message``, ``logger`` and the static
``service.name`` / ``service.version`` / ``env`` of the process. Fields bound
with :func:`log_context` / :func:`bind_log_context` (``request_id``, ``room``,
``player_id``...) are added to every record logged in that async
context, so all lines of one request or WebSocket connection can be joined.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

# Record attributes that are not user fields; ``color_message`` is uvicorn's ANSI copy.
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "color_message"}
# Session tokens in URLs (``/ws/ABCD?token=...``, logged by uvicorn) must not reach the logs.
_SECRET_QUERY = re.compile(r"([?&]token=)[^&\s\"']+")
_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})  # noqa: B039 - never mutated


def package_version() -> str:
    """The installed storyfeta version, the default ``service.version``."""
    try:
        return version("storyfeta")
    except PackageNotFoundError:
        return "unknown"


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
    """Render each record as one JSON object per line.

    ``static`` fields (service name, version, environment) go on every line.
    """

    def __init__(self, static: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._static = dict(static or {})

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "message": _SECRET_QUERY.sub(r"\1[redacted]", record.getMessage()),
            "logger": record.name,
            **self._static,
        }
        payload.update(_context.get())
        # Anything passed via ``extra={...}`` becomes a top-level field (and wins).
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    service_name: str,
    *,
    level: str = "INFO",
    service_version: str | None = None,
    env: str = "development",
) -> None:
    """Send every logger (including uvicorn's) through one JSON stdout handler.

    uvicorn's access log is switched off: its line is plain text with the status
    inside ``message``. :mod:`app.core.request_context` writes a structured one.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            {
                "service.name": service_name,
                "service.version": service_version or package_version(),
                "env": env,
            }
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
