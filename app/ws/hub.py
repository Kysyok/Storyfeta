"""Tracks this process's WebSocket connections and pushes personalised state.

Sockets are inherently per-process, but *state* is not: after any change the
service publishes the room code on a Redis channel. Every process receives it
and pushes a fresh snapshot to the sockets it holds for that room. That keeps
the app horizontally scalable and stateless apart from the open connections.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict

from fastapi import WebSocket
from redis.asyncio import Redis

from app.game.errors import NotFoundError
from app.game.snapshot import build_snapshot
from app.schemas.ws import ErrorMessage, PongMessage, StateMessage
from app.services.rooms import RoomService
from app.storage.base import EventPublisher
from app.storage.redis_store import EVENTS_CHANNEL, RedisRoomStore

log = logging.getLogger(__name__)

CLOSE_GOING_AWAY = 1001
CLOSE_ROOM_GONE = 4404
# How long the host may be gone (e.g. reloading the page) before someone else takes over.
HOST_GRACE_SECONDS = 10.0


class Connection:
    """One open socket. Sends are serialised because Starlette sockets aren't re-entrant."""

    def __init__(self, websocket: WebSocket, player_id: str) -> None:
        self.websocket = websocket
        self.player_id = player_id
        self._lock = asyncio.Lock()

    async def send(self, text: str) -> None:
        async with self._lock:
            await self.websocket.send_text(text)


class Hub:
    """Local connection registry plus the Redis pub/sub listener."""

    def __init__(
        self,
        rooms: RoomService,
        store: RedisRoomStore,
        publisher: EventPublisher,
        redis: Redis,
        host_grace_seconds: float = HOST_GRACE_SECONDS,
    ) -> None:
        self._rooms = rooms
        self._store = store
        self._publisher = publisher
        self._redis = redis
        self._connections: dict[str, set[Connection]] = defaultdict(set)
        self._listener: asyncio.Task[None] | None = None
        self._host_grace = host_grace_seconds
        self._host_checks: set[asyncio.Task[None]] = set()

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start listening for room-changed events."""
        self._listener = asyncio.create_task(self._listen(), name="hub-listener")

    async def stop(self) -> None:
        """Stop listening and close every socket cleanly (graceful shutdown)."""
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None
        for task in self._host_checks:
            task.cancel()
        await asyncio.gather(*self._host_checks, return_exceptions=True)
        self._host_checks.clear()
        connections = [c for group in self._connections.values() for c in group]
        await asyncio.gather(
            *(self._close(c, CLOSE_GOING_AWAY, "server restarting") for c in connections),
            return_exceptions=True,
        )
        self._connections.clear()

    # --- registration --------------------------------------------------------

    async def register(self, code: str, websocket: WebSocket, player_id: str) -> Connection:
        """Track a new socket, mark the player online and announce it."""
        connection = Connection(websocket, player_id)
        self._connections[code].add(connection)
        await self._store.presence_change(code, player_id, +1)
        await self._publisher.room_changed(code)
        self._schedule_host_check(code)
        return connection

    async def unregister(self, code: str, connection: Connection) -> None:
        """Forget a socket, mark the player offline (if last) and announce it."""
        self._connections[code].discard(connection)
        if not self._connections[code]:
            del self._connections[code]
        await self._store.presence_change(code, connection.player_id, -1)
        await self._publisher.room_changed(code)
        self._schedule_host_check(code)

    # --- sending -------------------------------------------------------------

    async def send_error(self, connection: Connection, code: str, message: str) -> None:
        """Tell one client an action was rejected."""
        await connection.send(ErrorMessage(code=code, message=message).model_dump_json())

    async def send_pong(self, connection: Connection) -> None:
        """Answer a client ping."""
        await connection.send(PongMessage().model_dump_json())

    async def push_room(self, code: str) -> None:
        """Send each local socket in ``code`` its own fresh snapshot."""
        group = list(self._connections.get(code, ()))
        if not group:
            return
        try:
            room = await self._rooms.get_room(code)
        except NotFoundError:
            await asyncio.gather(
                *(self._close(c, CLOSE_ROOM_GONE, "room closed") for c in group),
                return_exceptions=True,
            )
            return
        online = await self._store.online_ids(code)
        now = self._rooms.now()
        for connection in group:
            if room.player(connection.player_id) is None:
                continue
            snapshot = build_snapshot(room, connection.player_id, online, now, self._rooms.rules)
            try:
                await connection.send(StateMessage(state=snapshot).model_dump_json())
            except Exception:  # socket died mid-send; its handler will clean up
                log.debug("push failed", extra={"room": code})

    async def push_to(self, code: str, connection: Connection) -> None:
        """Send one socket its snapshot (used right after connecting)."""
        room = await self._rooms.get_room(code)
        online = await self._store.online_ids(code)
        snapshot = build_snapshot(
            room, connection.player_id, online, self._rooms.now(), self._rooms.rules
        )
        await connection.send(StateMessage(state=snapshot).model_dump_json())

    # --- internals -----------------------------------------------------------

    def _schedule_host_check(self, code: str) -> None:
        """After the grace period, hand host to an online player if the host is still away."""
        task = asyncio.create_task(self._check_host(code), name=f"host-check-{code}")
        self._host_checks.add(task)
        task.add_done_callback(self._host_checks.discard)

    async def _check_host(self, code: str) -> None:
        await asyncio.sleep(self._host_grace)
        try:
            await self._rooms.hand_off_host(code, await self._store.online_ids(code))
        except Exception:
            log.exception("host hand-off failed", extra={"room": code})

    async def _close(self, connection: Connection, code: int, reason: str) -> None:
        with contextlib.suppress(Exception):
            await connection.websocket.close(code=code, reason=reason)

    async def _listen(self) -> None:
        """Forward Redis room-changed events to local sockets; reconnect on failure."""
        while True:
            try:
                async with self._redis.pubsub() as pubsub:
                    await pubsub.subscribe(EVENTS_CHANNEL)
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            await self.push_room(str(message["data"]))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("event listener failed; retrying")
                await asyncio.sleep(1)
