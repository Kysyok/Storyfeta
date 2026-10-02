"""Interfaces the services depend on, so tests can swap Redis for memory."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from app.models.domain import Room


class RoomStore(Protocol):
    """Where active rooms live. The production implementation is Redis."""

    async def create(self, room: Room) -> bool:
        """Store a new room; return ``False`` if the code is already taken."""

    async def load(self, code: str) -> Room | None:
        """Fetch a room, or ``None`` if it does not exist (or expired)."""

    async def save(self, room: Room) -> None:
        """Persist a room and keep the deadline / activity indexes in sync."""

    def lock(self, code: str) -> AbstractAsyncContextManager[object]:
        """Exclusive lock for read-modify-write on one room, across processes."""

    async def due_rooms(self, now: float) -> list[str]:
        """Codes of rooms whose round deadline has passed."""

    async def stale_rooms(self, idle_before: float) -> list[str]:
        """Codes of rooms with no activity since ``idle_before``."""

    async def delete(self, code: str) -> None:
        """Remove a room and everything indexed under it."""


class EventPublisher(Protocol):
    """Tells every web process that a room changed so it can push new state."""

    async def room_changed(self, code: str) -> None:
        """Announce a change to room ``code``."""


class RoomArchiver(Protocol):
    """Saves a finished room permanently (PostgreSQL)."""

    async def archive(self, room: Room) -> None:
        """Persist the finished game for every signed-in player in it."""
