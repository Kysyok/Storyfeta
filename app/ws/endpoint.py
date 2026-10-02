"""The ``/ws/{code}`` endpoint: authenticate, register, then dispatch client messages."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core import security
from app.game.errors import GameError
from app.models.domain import Settings
from app.routers.deps import PLAYER_COOKIE_PREFIX, Container
from app.schemas import ws as msg
from app.ws.hub import Connection, Hub

log = logging.getLogger(__name__)
router = APIRouter()

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_ROOM_GONE = 4404


def _origin_ok(websocket: WebSocket) -> bool:
    """Reject cross-site WebSocket hijacking: Origin, if sent, must match Host."""
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    return urlparse(origin).netloc == websocket.headers.get("host")


async def _dispatch(
    container: Container, code: str, player_id: str, message: msg.ClientMessage
) -> None:
    """Route one validated client message to the room service."""
    rooms = container.rooms
    match message:
        case msg.SetSettings():
            await rooms.update_settings(
                code, player_id, Settings(rounds=message.rounds, turn_seconds=message.turn_seconds)
            )
        case msg.StartGame():
            await rooms.start_game(code, player_id)
        case msg.SubmitLine():
            await rooms.submit_line(code, player_id, message.text)
        case msg.SaveDraft():
            await rooms.save_draft(code, player_id, message.text)
        case msg.UnsubmitLine():
            await rooms.retract_line(code, player_id)
        case msg.RevealNext():
            await rooms.reveal_next(code, player_id)
        case msg.React():
            await rooms.react(code, player_id, message.line_id, message.emoji)
        case msg.Ping():
            pass  # answered by the caller


@router.websocket("/ws/{code}")
async def room_socket(websocket: WebSocket, code: str) -> None:
    """Live connection for one player in one room."""
    container: Container = websocket.app.state.container
    code = code.upper()
    await websocket.accept()

    if not _origin_ok(websocket):
        await websocket.close(code=CLOSE_FORBIDDEN)
        return

    token = websocket.cookies.get(PLAYER_COOKIE_PREFIX + code)
    token = token or websocket.query_params.get("token")
    try:
        if not token:
            raise security.InvalidToken("missing")
        claims = security.read_player_token(container.settings.secret_key.get_secret_value(), token)
        if claims.room_code != code:
            raise security.InvalidToken("wrong room")
        room = await container.rooms.get_room(code)
        if room.player(claims.player_id) is None:
            raise security.InvalidToken("not in room")
    except security.InvalidToken:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    except GameError:
        await websocket.close(code=CLOSE_ROOM_GONE)
        return

    hub: Hub = container.hub
    connection: Connection = await hub.register(code, websocket, claims.player_id)
    try:
        await hub.push_to(code, connection)
        while True:
            raw = await websocket.receive_text()
            try:
                message = msg.client_message_adapter.validate_json(raw)
            except ValidationError:
                await hub.send_error(connection, "bad_message", "That message wasn't understood.")
                continue
            if isinstance(message, msg.Ping):
                await hub.send_pong(connection)
                continue
            try:
                await _dispatch(container, code, claims.player_id, message)
            except GameError as exc:
                await hub.send_error(connection, exc.code, exc.message)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket crashed", extra={"room": code})
    finally:
        await hub.unregister(code, connection)
