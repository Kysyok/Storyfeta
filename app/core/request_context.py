"""ASGI middleware that gives every HTTP request and WebSocket a ``request_id``.

The id is taken from an incoming ``X-Request-ID`` header (set by an ingress or
proxy) when it looks sane, otherwise generated. It is bound into the log context
together with the room code parsed from the path, and echoed back in the
``X-Request-ID`` response header.

It also writes the access log (uvicorn's is switched off): one ``app.access``
line per HTTP request with ``method``, ``path``, ``route`` (the template, e.g.
``/api/rooms/{code}``), ``status``, ``duration_ms`` and ``client_ip``, and one
per WebSocket when it closes, with its lifetime and ``close_code``. 5xx log as
error and 4xx as warning; health probes log at debug so they don't drown the rest.

Wrap the whole application with it: a crash is then logged here, inside the
context, instead of by uvicorn after the context is gone.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import log_context

log = logging.getLogger(__name__)
access_log = logging.getLogger("app.access")
REQUEST_ID_HEADER = "x-request-id"
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_ROOM_PATH = re.compile(r"/(?:api/rooms|r|ws)/([A-Za-z0-9]+)(?:/|$)")
_PROBES = frozenset({"/healthz", "/readyz"})


def _incoming_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_ID.fullmatch(candidate) else None
    return None


def _level(path: str, status: int) -> int:
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.DEBUG if path in _PROBES else logging.INFO


def _log_access(
    scope: Scope, started_at: float, status: int | None, close_code: int | None
) -> None:
    path = scope["path"]
    fields: dict[str, Any] = {
        "method": scope["method"] if scope["type"] == "http" else "WEBSOCKET",
        "path": path,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
    }
    if route := getattr(scope.get("route"), "path", None):
        fields["route"] = route
    if client := scope.get("client"):
        fields["client_ip"] = client[0]
    if scope["type"] == "http":
        status = status or 500  # the app died before answering
        fields["status"] = status
        access_log.log(_level(path, status), f"{fields['method']} {path} {status}", extra=fields)
    else:
        fields["close_code"] = close_code
        access_log.info(f"WEBSOCKET {path} closed", extra=fields)


class RequestContextMiddleware:
    """Bind ``request_id`` (and ``room`` when the path names one) for the request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = _incoming_id(scope) or uuid.uuid4().hex
        fields = {"request_id": request_id}
        if match := _ROOM_PATH.match(scope["path"]):
            fields["room"] = match.group(1).upper()

        started_at = time.perf_counter()
        started = False
        status: int | None = None
        close_code: int | None = None

        async def send_with_id(message: Message) -> None:
            nonlocal started, status, close_code
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "websocket.close":
                close_code = message.get("code", 1000)
            if message["type"] in ("http.response.start", "websocket.accept"):
                started = True
                message["headers"] = [
                    *message.get("headers", []),
                    (REQUEST_ID_HEADER.encode(), request_id.encode()),
                ]
            await send(message)

        async def receive_noting_close() -> Message:
            nonlocal close_code
            message = await receive()
            if message["type"] == "websocket.disconnect" and close_code is None:
                close_code = message.get("code", 1000)
            return message

        with log_context(**fields):
            try:
                await self.app(scope, receive_noting_close, send_with_id)
            except Exception:
                if not started:
                    raise  # nothing was sent yet; let the server answer
                # Starlette already sent the 500; log the crash once, with context.
                log.exception("unhandled error", extra={"path": scope["path"]})
            finally:
                _log_access(scope, started_at, status, close_code)
