"""Scheduled clean-up worker: ``python -m app.worker``.

Redis TTLs already expire abandoned rooms; this process also removes rooms that
have been idle for a while (and their timer/presence keys) so nothing lingers.
Stop it with SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.rooms import RoomService
from app.storage.redis_store import RedisEventPublisher, RedisRoomStore

log = logging.getLogger("app.worker")


async def run() -> None:
    """Delete idle rooms every ``CLEANUP_INTERVAL_SECONDS`` until told to stop."""
    settings = get_settings()
    configure_logging(settings.log_level)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    rooms = RoomService(
        RedisRoomStore(redis, settings.room_ttl_seconds),
        RedisEventPublisher(redis),
        settings.rules,
        settings.default_game_settings,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    log.info("worker started")
    while not stop.is_set():
        try:
            removed = await rooms.delete_idle(settings.room_idle_seconds)
            if removed:
                log.info("deleted idle rooms", extra={"count": removed})
        except Exception:
            log.exception("cleanup pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.cleanup_interval_seconds)
        except TimeoutError:
            pass
    await redis.aclose()
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(run())
