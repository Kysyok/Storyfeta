"""In-memory stand-ins for Redis and PostgreSQL used by the service tests."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict

from app.models.domain import Phase, Room


class MemoryStore:
    """Same behaviour as ``RedisRoomStore`` but in a dict. Rooms are JSON round-tripped."""

    def __init__(self) -> None:
        self.rooms: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.deadlines: dict[str, float] = {}
        self.activity: dict[str, float] = {}

    async def create(self, room: Room) -> bool:
        if room.code in self.rooms:
            return False
        self.rooms[room.code] = json.dumps(room.to_dict())
        self.activity[room.code] = room.updated_at
        return True

    async def load(self, code: str) -> Room | None:
        raw = self.rooms.get(code)
        return Room.from_dict(json.loads(raw)) if raw else None

    async def save(self, room: Room) -> None:
        self.rooms[room.code] = json.dumps(room.to_dict())
        self.activity[room.code] = room.updated_at
        if room.phase is Phase.WRITING and room.round_deadline is not None:
            self.deadlines[room.code] = room.round_deadline
        else:
            self.deadlines.pop(room.code, None)

    def lock(self, code: str) -> asyncio.Lock:
        return self._locks[code]

    async def due_rooms(self, now: float) -> list[str]:
        return [c for c, d in self.deadlines.items() if d <= now]

    async def stale_rooms(self, idle_before: float) -> list[str]:
        return [c for c, t in self.activity.items() if t <= idle_before]

    async def delete(self, code: str) -> None:
        self.rooms.pop(code, None)
        self.deadlines.pop(code, None)
        self.activity.pop(code, None)


class RecordingPublisher:
    """Collects the codes of rooms that changed."""

    def __init__(self) -> None:
        self.changed: list[str] = []

    async def room_changed(self, code: str) -> None:
        self.changed.append(code)


class RecordingArchiver:
    """Collects archived rooms; can be told to fail."""

    def __init__(self, fail: bool = False) -> None:
        self.archived: list[Room] = []
        self.fail = fail

    async def archive(self, room: Room) -> None:
        if self.fail:
            raise RuntimeError("database down")
        self.archived.append(room)
