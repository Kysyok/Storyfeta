"""Request-scoped log context: JSON fields and the request-id middleware."""

from __future__ import annotations

import asyncio
import json
import logging

from app.core.logging import (
    JsonFormatter,
    bind_log_context,
    current_log_context,
    log_context,
)
from app.core.request_context import RequestContextMiddleware


def _render(msg: str, **extra: object) -> dict[str, object]:
    record = logging.makeLogRecord({"msg": msg, "levelname": "INFO", **extra})
    return json.loads(JsonFormatter().format(record))


def test_context_fields_appear_in_json() -> None:
    with log_context(request_id="abc", room="WXYZ"):
        line = _render("hello")
    assert line["request_id"] == "abc"
    assert line["room"] == "WXYZ"
    assert "request_id" not in _render("outside")


def test_explicit_extra_wins_over_context() -> None:
    with log_context(room="AAAA"):
        assert _render("x", room="BBBB")["room"] == "BBBB"


def test_bind_is_undone_when_block_exits() -> None:
    with log_context(request_id="r1"):
        bind_log_context(player_id="p1")
        assert current_log_context() == {"request_id": "r1", "player_id": "p1"}
    assert current_log_context() == {}


async def _call(scope: dict, app_seen: list) -> list[dict]:
    async def app(scope, receive, send) -> None:
        app_seen.append(current_log_context())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request"}

    async def send(message: dict) -> None:
        sent.append(message)

    await RequestContextMiddleware(app)(scope, receive, send)
    return sent


def _scope(path: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "path": path, "headers": headers or []}


def test_middleware_generates_id_and_parses_room() -> None:
    seen: list[dict] = []
    sent = asyncio.run(_call(_scope("/api/rooms/abcd/join"), seen))
    ctx = seen[0]
    assert ctx["room"] == "ABCD"
    assert len(ctx["request_id"]) == 32
    assert (b"x-request-id", ctx["request_id"].encode()) in sent[0]["headers"]
    assert current_log_context() == {}


def test_middleware_reuses_valid_incoming_id() -> None:
    seen: list[dict] = []
    asyncio.run(_call(_scope("/ws/WXYZ", [(b"x-request-id", b"from-ingress.1")]), seen))
    assert seen[0] == {"request_id": "from-ingress.1", "room": "WXYZ"}


def test_middleware_rejects_garbage_incoming_id() -> None:
    seen: list[dict] = []
    asyncio.run(_call(_scope("/", [(b"x-request-id", b"bad id\n{}")]), seen))
    assert seen[0]["request_id"] != "bad id\n{}"
    assert "room" not in seen[0]


class _JsonLines(logging.Handler):
    """Formats on emit, like the real stdout handler (i.e. inside the context)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


def test_middleware_logs_crash_after_500_with_context() -> None:
    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 500, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        raise RuntimeError("boom")

    async def noop(*_: object) -> None:
        return None

    scope = _scope("/r/abcd", [(b"x-request-id", b"req-1")])
    handler = _JsonLines()
    logger = logging.getLogger("app.core.request_context")
    logger.addHandler(handler)
    try:
        asyncio.run(RequestContextMiddleware(app)(scope, noop, noop))
    finally:
        logger.removeHandler(handler)
    [line] = handler.lines
    assert line["msg"] == "unhandled error"
    assert line["request_id"] == "req-1"
    assert line["room"] == "ABCD"
