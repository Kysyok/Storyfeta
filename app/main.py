"""Application factory: wiring, lifecycle and graceful shutdown.

Startup builds the service container (Redis, optional PostgreSQL, room service,
timer loop, WebSocket hub). Shutdown reverses it: stop the timers, close every
WebSocket with 1001 "going away" so clients reconnect elsewhere, then release
connections. All state is in Redis, so nothing is lost.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.game.errors import GameError
from app.routers import accounts, pages, rooms
from app.routers.deps import Container
from app.services.accounts import AccountService
from app.services.archive import ArchiveService
from app.services.rooms import RoomService
from app.services.timers import TimerLoop
from app.storage.redis_store import RedisEventPublisher, RedisRoomStore
from app.ws import endpoint
from app.ws.hub import Hub

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def build_container() -> Container:
    """Create every service from environment settings."""
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    store = RedisRoomStore(redis, settings.room_ttl_seconds)
    publisher = RedisEventPublisher(redis)

    accounts: AccountService | None = None
    archiver: ArchiveService | None = None
    db_engine = None
    if settings.database_url:
        from app.storage.database import create_engine, create_session_factory

        db_engine = create_engine(settings.database_url)
        sessions = create_session_factory(db_engine)
        accounts = AccountService(sessions, settings.rules)
        archiver = ArchiveService(sessions)

    rooms_service = RoomService(
        store,
        publisher,
        settings.rules,
        settings.default_game_settings,
        settings.room_code_length,
        archiver,
    )
    return Container(
        settings=settings,
        redis=redis,
        store=store,
        rooms=rooms_service,
        timers=TimerLoop(rooms_service, settings.timer_poll_seconds),
        hub=Hub(rooms_service, store, publisher, redis),
        accounts=accounts,
        db_engine=db_engine,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start background tasks on boot; stop them cleanly on SIGTERM."""
    container = build_container()
    app.state.container = container
    container.hub.start()
    container.timers.start()
    log.info("started", extra={"accounts": container.settings.accounts_enabled})
    try:
        yield
    finally:
        log.info("shutting down")
        await container.timers.stop()
        await container.hub.stop()
        await container.redis.aclose()
        if container.db_engine is not None:
            await container.db_engine.dispose()
        log.info("stopped")


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    configure_logging(get_settings().log_level)
    app = FastAPI(
        title="Storyfeta",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(GameError)
    async def game_error_handler(_: Request, exc: GameError) -> JSONResponse:
        return JSONResponse(
            {"code": exc.code, "message": exc.message}, status_code=exc.status_code
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(pages.router)
    app.include_router(rooms.router)
    app.include_router(accounts.router)
    app.include_router(endpoint.router)
    return app


app = create_app()
