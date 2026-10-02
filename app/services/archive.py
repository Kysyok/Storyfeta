"""Copies finished games from Redis state into PostgreSQL, for each signed-in player."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.game.archive import build_archive_payload
from app.models.domain import Room
from app.models.orm import ArchivedGame


class ArchiveService:
    """Implements :class:`app.storage.base.RoomArchiver`."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def archive(self, room: Room) -> None:
        """Add the finished game to the history of every signed-in player in it.

        Each account gets its own copy, so a player's history survives independently
        of who hosted. Several tabs of the same account produce a single entry.
        """
        account_ids = {p.account_id for p in room.players if p.account_id}
        if not account_ids:
            return
        payload = build_archive_payload(room)
        finished_at = datetime.now(UTC)
        async with self._sessions() as session:
            session.add_all(
                ArchivedGame(
                    account_id=uuid.UUID(account_id),
                    room_code=room.code,
                    rounds=room.settings.rounds,
                    finished_at=finished_at,
                    payload=payload,
                )
                for account_id in sorted(account_ids)
            )
            await session.commit()
