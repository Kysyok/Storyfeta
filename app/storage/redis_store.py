"""Redis-backed room storage, presence and change events.

Key layout (all prefixed ``sf:``)::

    sf:room:{code}      JSON document of the Room, with a TTL
    sf:lock:{code}      per-room lock guarding read-modify-write
    sf:deadlines        ZSET code -> round deadline (epoch seconds)
    sf:index            ZSET code -> last activity (epoch seconds)
    sf:presence:{code}  HASH player_id -> open WebSocket count (all processes)
    sf:events           pub/sub channel; payload is the changed room code

Because the deadlines live in Redis, any web process can fire a due timer and a
restarted process picks them all up again -- no timer state is kept in memory.
"""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager

from redis.asyncio import Redis

from app.models.domain import Phase, Room

EVENTS_CHANNEL = "sf:events"
_DEADLINES = "sf:deadlines"
_INDEX = "sf:index"


def _room_key(code: str) -> str:
    return f"sf:room:{code}"


def _presence_key(code: str) -> str:
    return f"sf:presence:{code}"


class RedisRoomStore:
    """Implements :class:`app.storage.base.RoomStore` on Redis."""

    def __init__(self, redis: Redis, room_ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = room_ttl_seconds

    async def create(self, room: Room) -> bool:
        created = await self._redis.set(
            _room_key(room.code), json.dumps(room.to_dict()), ex=self._ttl, nx=True
        )
        if created:
            await self._redis.zadd(_INDEX, {room.code: room.updated_at})
        return bool(created)

    async def load(self, code: str) -> Room | None:
        raw = await self._redis.get(_room_key(code))
        return Room.from_dict(json.loads(raw)) if raw else None

    async def save(self, room: Room) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(_room_key(room.code), json.dumps(room.to_dict()), ex=self._ttl)
            pipe.zadd(_INDEX, {room.code: room.updated_at})
            if room.phase is Phase.WRITING and room.round_deadline is not None:
                pipe.zadd(_DEADLINES, {room.code: room.round_deadline})
            else:
                pipe.zrem(_DEADLINES, room.code)
            await pipe.execute()

    def lock(self, code: str) -> AbstractAsyncContextManager[object]:
        return self._redis.lock(f"sf:lock:{code}", timeout=10, blocking_timeout=10)

    async def due_rooms(self, now: float) -> list[str]:
        return list(await self._redis.zrangebyscore(_DEADLINES, "-inf", now))

    async def stale_rooms(self, idle_before: float) -> list[str]:
        return list(await self._redis.zrangebyscore(_INDEX, "-inf", idle_before))

    async def delete(self, code: str) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(_room_key(code), _presence_key(code))
            pipe.zrem(_DEADLINES, code)
            pipe.zrem(_INDEX, code)
            await pipe.execute()

    # --- presence ----------------------------------------------------------

    async def presence_change(self, code: str, player_id: str, delta: int) -> None:
        """Count one more (``+1``) or fewer (``-1``) open sockets for a player."""
        key = _presence_key(code)
        count = await self._redis.hincrby(key, player_id, delta)
        if count <= 0:
            await self._redis.hdel(key, player_id)
        await self._redis.expire(key, self._ttl)

    async def online_ids(self, code: str) -> set[str]:
        """Players with at least one open socket on any process."""
        counts = await self._redis.hgetall(_presence_key(code))
        return {pid for pid, n in counts.items() if int(n) > 0}


class RedisEventPublisher:
    """Implements :class:`app.storage.base.EventPublisher` with Redis pub/sub."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def room_changed(self, code: str) -> None:
        await self._redis.publish(EVENTS_CHANNEL, code)
