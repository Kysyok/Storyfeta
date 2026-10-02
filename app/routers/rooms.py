"""REST endpoints for creating and joining rooms. Everything after that is WebSocket."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.core import security
from app.game.errors import GameError
from app.models.domain import Phase, Room, Settings
from app.routers.deps import (
    Container,
    account_id_from_request,
    get_container,
    player_id_from_request,
    set_player_cookie,
)
from app.models.orm import Account
from app.schemas.http import CreateRoomRequest, JoinRoomRequest, RoomInfo, RoomJoined

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


def _joined(container: Container, response: Response, code: str, player_id: str) -> RoomJoined:
    """Issue the signed session token (cookie + body) for a player."""
    settings = container.settings
    token = security.create_player_token(
        settings.secret_key.get_secret_value(),
        player_id,
        code,
        settings.player_token_ttl_seconds,
    )
    set_player_cookie(response, settings, code, token)
    return RoomJoined(code=code, player_id=player_id, url=f"/r/{code}", token=token)


async def _account(request: Request, container: Container) -> Account | None:
    """The signed-in account, or ``None`` for guests (and for stale cookies)."""
    account_id = account_id_from_request(request, container.settings)
    if account_id and container.accounts:
        return await container.accounts.get(account_id)
    return None


def _nickname(typed: str | None, account: Account | None) -> str:
    """A nickname typed in the form wins; otherwise use the profile nickname."""
    if typed and typed.strip():
        return typed
    if account is not None:
        return account.nickname
    raise GameError("nickname_empty", "Enter a nickname to join.")


def _returning_player(
    request: Request, container: Container, room: Room, account: Account | None
) -> str | None:
    """The id of this visitor's existing seat in ``room``, if they have one."""
    player_id = player_id_from_request(request, container.settings, room.code)
    if player_id and room.player(player_id):
        return player_id
    if account is not None:
        return next((p.id for p in room.players if p.account_id == str(account.id)), None)
    return None


@router.post("", response_model=RoomJoined, status_code=201)
async def create_room(
    body: CreateRoomRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
) -> RoomJoined:
    """Create a room as host. A signed-in host gets their saved defaults and archive."""
    account = await _account(request, container)
    settings: Settings | None = None
    if account is not None:
        settings = Settings(
            rounds=account.default_rounds, turn_seconds=account.default_turn_seconds
        )
    player_id = security.new_player_id()
    room = await container.rooms.create_room(
        player_id,
        _nickname(body.nickname, account),
        settings,
        str(account.id) if account else None,
    )
    return _joined(container, response, room.code, player_id)


@router.post("/{code}/join", response_model=RoomJoined)
async def join_room(
    code: str,
    body: JoinRoomRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
) -> RoomJoined:
    """Join a room's lobby: a guest needs only a nickname; signed-in players need nothing.

    Someone who already has a seat here (same browser, or same signed-in account)
    gets that seat back instead of a new one -- even after the game has started.
    """
    code = code.upper()
    account = await _account(request, container)
    room = await container.rooms.get_room(code)
    returning = _returning_player(request, container, room, account)
    if returning is not None:
        return _joined(container, response, code, returning)
    player_id = security.new_player_id()
    await container.rooms.join_room(
        code,
        player_id,
        _nickname(body.nickname, account),
        str(account.id) if account else None,
    )
    return _joined(container, response, code, player_id)


@router.get("/{code}", response_model=RoomInfo)
async def room_info(code: str, container: Container = Depends(get_container)) -> RoomInfo:
    """Public facts about a room (no player data)."""
    room = await container.rooms.get_room(code)
    return RoomInfo(
        code=room.code,
        phase=room.phase.value,
        player_count=len(room.players),
        joinable=room.phase is Phase.LOBBY
        and len(room.players) < container.settings.max_players,
    )
