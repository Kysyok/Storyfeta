"""HTML pages (Jinja2). Pages are thin shells; live behaviour is in /static/js."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.core.assets import static_url
from app.game.errors import GameError
from app.models.domain import Phase
from app.routers.deps import (
    Container,
    account_id_from_request,
    get_container,
    player_id_from_request,
)

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.globals["static"] = static_url


async def _render(
    request: Request, container: Container, name: str, status: int = 200, **ctx: object
) -> HTMLResponse:
    """Render a template with the flags every page needs (sign-in state, profile nickname)."""
    nickname: str | None = None
    account_id = account_id_from_request(request, container.settings)
    if account_id and container.accounts:
        account = await container.accounts.get(account_id)
        nickname = account.nickname if account else None
    context = {
        "accounts_enabled": container.settings.accounts_enabled,
        "signed_in": nickname is not None,
        "account_nickname": nickname,
        "code_length": container.settings.room_code_length,
        **ctx,
    }
    return templates.TemplateResponse(request, name, context, status_code=status)


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, container: Container = Depends(get_container)) -> HTMLResponse:
    """Landing page: create a room or enter a code."""
    return await _render(request, container, "index.html")


@router.get("/r/{code}", response_class=HTMLResponse)
async def room_page(
    code: str, request: Request, container: Container = Depends(get_container)
) -> HTMLResponse:
    """The game screen for players already in the room; a nickname form for everyone else."""
    code = code.upper()
    try:
        room = await container.rooms.get_room(code)
    except GameError:
        return await _render(request, container, "missing_room.html", 404, code=code)

    player_id = player_id_from_request(request, container.settings, code)
    if player_id and room.player(player_id):
        return await _render(request, container, "room.html", code=code)

    joinable = room.phase is Phase.LOBBY and len(room.players) < container.settings.max_players
    return await _render(
        request,
        container,
        "join.html",
        code=code,
        joinable=joinable,
        host_name=room.player(room.host_id).nickname,
    )


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request, container: Container = Depends(get_container)
) -> HTMLResponse:
    """Sign in / register, saved defaults and the archive list."""
    if not container.settings.accounts_enabled:
        return await _render(request, container, "missing_room.html", 404, code="")
    return await _render(request, container, "account.html")


@router.get("/archive/{game_id}", response_class=HTMLResponse)
async def archive_page(
    game_id: str, request: Request, container: Container = Depends(get_container)
) -> HTMLResponse:
    """A saved game rendered in full; data is loaded by JS from /api/archive/{id}."""
    if not container.settings.accounts_enabled:
        return await _render(request, container, "missing_room.html", 404, code="")
    return await _render(request, container, "archive.html", game_id=game_id)


@router.get("/readyz")
async def readyz(container: Container = Depends(get_container)) -> JSONResponse:
    """Readiness: only Redis is required to serve games; PostgreSQL backs accounts alone."""
    try:
        await container.redis.ping()
    except Exception:
        return JSONResponse({"status": "unavailable", "redis": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok", "redis": "ok"})


@router.get("/healthz")
async def healthz(container: Container = Depends(get_container)) -> JSONResponse:
    """Full health for monitoring: Redis and (when configured) PostgreSQL must both answer."""
    checks: dict[str, str] = {}
    try:
        await container.redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    if container.db_engine is not None:
        try:
            async with container.db_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception:
            checks["postgres"] = "unavailable"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "unavailable", **checks},
        status_code=200 if healthy else 503,
    )
