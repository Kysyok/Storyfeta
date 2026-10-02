"""Background loop that fires round timers.

The loop keeps no timers in memory. Each pass it asks the store which rooms have
a deadline in the past and ticks them; ``RoomService.tick`` is idempotent and
lock-protected, so several web processes can run this loop at once and a freshly
restarted process resumes exactly where the last one stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.services.rooms import RoomService

log = logging.getLogger(__name__)


class TimerLoop:
    """Polls for overdue rounds until stopped."""

    def __init__(self, rooms: RoomService, poll_seconds: float) -> None:
        self._rooms = rooms
        self._poll = poll_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Begin polling in the background."""
        self._task = asyncio.create_task(self._run(), name="timer-loop")

    async def stop(self) -> None:
        """Cancel the loop and wait for it to finish."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._rooms.tick_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("timer pass failed")
            await asyncio.sleep(self._poll)
