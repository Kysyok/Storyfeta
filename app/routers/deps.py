"""Shared request helpers: the service container, cookies and current identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import security
from app.core.config import Settings
from app.game.errors import ForbiddenError
from app.services.accounts import AccountService
from app.services.rooms import RoomService
from app.services.timers import TimerLoop
from app.storage.redis_store import RedisRoomStore

if TYPE_CHECKING:
    from app.ws.hub import Hub

PLAYER_COOKIE_PREFIX = "sf_player_"
ACCOUNT_COOKIE = "sf_account"


@dataclass
class Container:
    """Everything the app wires together at startup; stored on ``app.state``."""

    settings: Settings
    redis: Redis
    store: RedisRoomStore
    rooms: RoomService
    timers: TimerLoop
    hub: Hub
    accounts: AccountService | None = None
    db_engine: AsyncEngine | None = field(default=None)


def get_container(request: Request) -> Container:
    """FastAPI dependency returning the container."""
    return request.app.state.container  # type: ignore[no-any-return]


def set_player_cookie(response: Response, settings: Settings, code: str, token: str) -> None:
    """Attach the room-scoped player session cookie."""
    response.set_cookie(
        PLAYER_COOKIE_PREFIX + code,
        token,
        max_age=settings.player_token_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def set_account_cookie(response: Response, settings: Settings, token: str) -> None:
    """Attach the account session cookie."""
    response.set_cookie(
        ACCOUNT_COOKIE,
        token,
        max_age=settings.account_token_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def player_id_from_request(request: Request, settings: Settings, code: str) -> str | None:
    """The player id in this room's cookie, or ``None`` if absent/invalid."""
    token = request.cookies.get(PLAYER_COOKIE_PREFIX + code)
    if not token:
        return None
    try:
        claims = security.read_player_token(settings.secret_key.get_secret_value(), token)
    except security.InvalidToken:
        return None
    return claims.player_id if claims.room_code == code else None


def account_id_from_request(request: Request, settings: Settings) -> str | None:
    """The signed-in account id, or ``None`` for guests."""
    token = request.cookies.get(ACCOUNT_COOKIE)
    if not token:
        return None
    try:
        return security.read_account_token(settings.secret_key.get_secret_value(), token)
    except security.InvalidToken:
        return None


def require_account_id(request: Request, settings: Settings) -> str:
    """Like :func:`account_id_from_request` but raises for guests."""
    account_id = account_id_from_request(request, settings)
    if account_id is None:
        raise ForbiddenError("sign_in_required", "Sign in to do that.")
    return account_id
