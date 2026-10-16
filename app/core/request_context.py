"""ASGI middleware that gives every HTTP request and WebSocket a ``request_id``.

The id is taken from an incoming ``X-Request-ID`` header (set by an ingress or
proxy) when it looks sane, otherwise generated. It is bound into the log context
together with the room code parsed from the path, and echoed back in the
``X-Request-ID`` response header. uvicorn writes its access log from inside the
app's ``send`` call, so access lines carry the same fields.

Wrap the whole application with it: a crash is then logged here, inside the
context, instead of by uvicorn after the context is gone.
"""

from __future__ import annotations

import logging
import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import log_context

log = logging.getLogger(__name__)
REQUEST_ID_HEADER = "x-request-id"
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_ROOM_PATH = re.compile(r"/(?:api/rooms|r|ws)/([A-Za-z0-9]+)(?:/|$)")


def _incoming_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_ID.fullmatch(candidate) else None
    return None


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

        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] in ("http.response.start", "websocket.accept"):
                started = True
                message["headers"] = [
                    *message.get("headers", []),
                    (REQUEST_ID_HEADER.encode(), request_id.encode()),
                ]
            await send(message)

        with log_context(**fields):
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                if not started:
                    raise  # nothing was sent yet; let the server answer
                # Starlette already sent the 500; log the crash once, with context.
                log.exception("unhandled error", extra={"path": scope["path"]})
