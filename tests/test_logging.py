"""JSON log schema, request-scoped log context and the request/access middleware."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

from app.core.logging import (
    JsonFormatter,
    bind_log_context,
    current_log_context,
    log_context,
)
from app.core.request_context import RequestContextMiddleware

STATIC = {"service.name": "storyfeta-web", "service.version": "1.2.3", "env": "test"}


def _render(msg: str, **extra: object) -> dict[str, object]:
    record = logging.makeLogRecord({"msg": msg, "levelname": "INFO", **extra})
    return json.loads(JsonFormatter(STATIC).format(record))


class _JsonLines(logging.Handler):
    """Formats on emit, like the real stdout handler (i.e. inside the context)."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(JsonFormatter(STATIC))
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@contextmanager
def _captured(*names: str) -> Iterator[list[dict]]:
    handler = _JsonLines()
    loggers = [logging.getLogger(name) for name in names]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    try:
        yield handler.lines
    finally:
        for logger, level in zip(loggers, levels, strict=True):
            logger.removeHandler(handler)
            logger.setLevel(level)


# --- schema ------------------------------------------------------------------


def test_line_has_the_seminar_fields() -> None:
    line = _render("hello %s", args=("world",))
    assert line["message"] == "hello world"
    assert line["level"] == "info"
    assert line["timestamp"].endswith("+00:00")
    assert line["service.name"] == "storyfeta-web"
    assert line["service.version"] == "1.2.3"
    assert line["env"] == "test"


def test_uvicorn_color_message_is_dropped() -> None:
    assert "color_message" not in _render("x", color_message="\x1b[36mx\x1b[0m")


def test_session_token_in_url_is_redacted() -> None:
    url = "/ws/ABCD?token=eyJ.a.b&x=1"
    line = _render('%s - "WebSocket %s" [accepted]', args=("10.0.0.1", url))
    assert line["message"] == '10.0.0.1 - "WebSocket /ws/ABCD?token=[redacted]&x=1" [accepted]'


def test_exception_is_one_field() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        line = _render("failed", exc_info=sys.exc_info())
    assert "ValueError: boom" in line["exception"]


# --- context -----------------------------------------------------------------


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


# --- middleware --------------------------------------------------------------


def _scope(path: str, headers: list[tuple[bytes, bytes]] | None = None, **extra: object) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers or [],
        "client": ("10.0.0.7", 51234),
        **extra,
    }


def _run(app, scope: dict, receive_messages: list[dict] | None = None) -> list[dict]:
    sent: list[dict] = []
    pending = list(receive_messages or [{"type": "http.request"}])

    async def receive() -> dict:
        return pending.pop(0)

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(RequestContextMiddleware(app)(scope, receive, send))
    return sent


def _answering(status: int, seen: list[dict] | None = None, route: str | None = None):
    async def app(scope, receive, send) -> None:
        if seen is not None:
            seen.append(current_log_context())
        if route:
            scope["route"] = SimpleNamespace(path=route)  # what Starlette's router sets
        bind_log_context(player_id="p1")
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app


def test_middleware_generates_id_and_parses_room() -> None:
    seen: list[dict] = []
    sent = _run(_answering(200, seen), _scope("/api/rooms/abcd/join"))
    ctx = seen[0]
    assert ctx["room"] == "ABCD"
    assert len(ctx["request_id"]) == 32
    assert (b"x-request-id", ctx["request_id"].encode()) in sent[0]["headers"]
    assert current_log_context() == {}


def test_middleware_reuses_valid_incoming_id() -> None:
    seen: list[dict] = []
    _run(_answering(200, seen), _scope("/ws/WXYZ", [(b"x-request-id", b"from-ingress.1")]))
    assert seen[0] == {"request_id": "from-ingress.1", "room": "WXYZ"}


def test_middleware_rejects_garbage_incoming_id() -> None:
    seen: list[dict] = []
    _run(_answering(200, seen), _scope("/", [(b"x-request-id", b"bad id\n{}")]))
    assert seen[0]["request_id"] != "bad id\n{}"
    assert "room" not in seen[0]


def test_access_line_is_structured() -> None:
    scope = _scope("/api/rooms/abcd", [(b"x-request-id", b"req-7")])
    with _captured("app.access") as lines:
        _run(_answering(200, route="/api/rooms/{code}"), scope)
    [line] = lines
    assert line["message"] == "GET /api/rooms/abcd 200"
    assert line["level"] == "info"
    assert line["method"] == "GET"
    assert line["path"] == "/api/rooms/abcd"
    assert line["route"] == "/api/rooms/{code}"
    assert line["status"] == 200
    assert line["duration_ms"] >= 0
    assert line["client_ip"] == "10.0.0.7"
    # Context bound by the handler is still there when the access line is written.
    assert (line["request_id"], line["room"], line["player_id"]) == ("req-7", "ABCD", "p1")


def test_access_level_follows_status_and_probes_are_quiet() -> None:
    with _captured("app.access") as lines:
        _run(_answering(503), _scope("/x"))
        _run(_answering(404), _scope("/x"))
        _run(_answering(200), _scope("/readyz"))
        _run(_answering(503), _scope("/readyz"))
    assert [line["level"] for line in lines] == ["error", "warning", "debug", "error"]


def test_websocket_access_line_on_close() -> None:
    async def app(scope, receive, send) -> None:
        await send({"type": "websocket.accept"})
        await receive()  # client goes away

    scope = _scope("/ws/abcd", type="websocket")
    del scope["method"]
    with _captured("app.access") as lines:
        sent = _run(app, scope, [{"type": "websocket.disconnect", "code": 1001}])
    assert sent[0]["headers"][0][0] == b"x-request-id"
    [line] = lines
    assert line["method"] == "WEBSOCKET"
    assert line["close_code"] == 1001
    assert line["room"] == "ABCD"
    assert "status" not in line


def test_middleware_logs_crash_after_500_with_context() -> None:
    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 500, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        raise RuntimeError("boom")

    scope = _scope("/r/abcd", [(b"x-request-id", b"req-1")])
    with _captured("app.core.request_context", "app.access") as lines:
        _run(app, scope)
    crash, access = lines
    assert crash["message"] == "unhandled error"
    assert "RuntimeError: boom" in crash["exception"]
    assert (crash["request_id"], crash["room"]) == ("req-1", "ABCD")
    assert (access["status"], access["level"], access["request_id"]) == (500, "error", "req-1")
