"""Room use-cases: the glue between the pure engine, storage and notifications.

Every mutation follows the same recipe: take the room's lock, load it, apply a
pure engine function, save it, then tell every web process the room changed.
Holding the lock makes the timer loop (which may run in several processes) and
player actions safe to interleave.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable

from app.game import engine
from app.game.errors import ForbiddenError, NotFoundError
from app.models.domain import Phase, Player, Room, Settings
from app.storage.base import EventPublisher, RoomArchiver, RoomStore

log = logging.getLogger(__name__)

# No 0/O/1/I so codes are easy to read aloud across the table.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_ATTEMPTS = 25


class RoomService:
    """High-level operations on rooms. Stateless: everything lives in the store."""

    def __init__(
        self,
        store: RoomStore,
        publisher: EventPublisher,
        rules: engine.Rules,
        default_settings: Settings,
        code_length: int = 4,
        archiver: RoomArchiver | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._rules = rules
        self._defaults = default_settings
        self._code_length = code_length
        self._archiver = archiver
        self._clock = clock

    @property
    def rules(self) -> engine.Rules:
        """Limits in force (used when building snapshots)."""
        return self._rules

    def now(self) -> float:
        """Current time in epoch seconds."""
        return self._clock()

    # --- reads ---------------------------------------------------------------

    async def get_room(self, code: str) -> Room:
        """Load a room or raise :class:`NotFoundError`."""
        room = await self._store.load(code.upper())
        if room is None:
            raise NotFoundError("no_such_room", "We couldn't find a room with that code.")
        return room

    # --- lobby ---------------------------------------------------------------

    async def create_room(
        self,
        player_id: str,
        nickname: str,
        settings: Settings | None = None,
        account_id: str | None = None,
    ) -> Room:
        """Create a room with ``player_id`` as host. Retries on code collisions."""
        for _ in range(_CODE_ATTEMPTS):
            code = "".join(
                secrets.choice(CODE_ALPHABET) for _ in range(self._code_length)
            )
            room = engine.new_room(
                code,
                player_id,
                nickname,
                settings or self._defaults,
                self.now(),
                account_id,
                self._rules,
            )
            if await self._store.create(room):
                log.info("room created", extra={"room": code})
                return room
        raise RuntimeError("could not find a free room code")

    async def join_room(
        self, code: str, player_id: str, nickname: str, account_id: str | None = None
    ) -> Room:
        """Add a player to a room's lobby (``account_id`` if they are signed in)."""
        return await self._mutate(
            code,
            lambda room, now: engine.add_player(
                room, player_id, nickname, now, self._rules, account_id
            ),
        )

    # --- actions (each returns nothing; state is pushed via the publisher) ---------

    async def update_settings(self, code: str, actor_id: str, settings: Settings) -> None:
        await self._mutate(
            code,
            lambda room, now: engine.update_settings(
                room, actor_id, settings, now, self._rules
            ),
            actor_id,
        )

    async def start_game(self, code: str, actor_id: str) -> None:
        await self._mutate(
            code,
            lambda room, now: engine.start_game(room, actor_id, now, self._rules),
            actor_id,
        )

    async def submit_line(self, code: str, actor_id: str, text: str) -> None:
        await self._mutate(
            code,
            lambda room, now: engine.submit_line(room, actor_id, text, now, self._rules),
            actor_id,
        )

    async def retract_line(self, code: str, actor_id: str) -> None:
        """Take the player's passed-on line back so they can edit it."""
        await self._mutate(
            code,
            lambda room, now: engine.retract_line(room, actor_id, now, self._rules),
            actor_id,
        )

    async def save_draft(self, code: str, actor_id: str, text: str) -> None:
        """Store the line being typed. Nobody else's screen changes, so nothing is broadcast."""
        await self._mutate(
            code,
            lambda room, now: engine.save_draft(room, actor_id, text, now, self._rules),
            actor_id,
            notify=False,
        )

    async def reveal_next(self, code: str, actor_id: str) -> None:
        await self._mutate(
            code, lambda room, now: engine.reveal_next(room, actor_id, now), actor_id
        )

    async def react(self, code: str, actor_id: str, line_id: str, emoji: str) -> None:
        await self._mutate(
            code,
            lambda room, now: engine.toggle_reaction(
                room, actor_id, line_id, emoji, now, self._rules
            ),
            actor_id,
        )

    async def hand_off_host(self, code: str, online: set[str]) -> bool:
        """Pass host to an online player if the host has left. Returns whether it moved."""
        async with self._store.lock(code):
            room = await self._store.load(code)
            if room is None or engine.hand_off_host(room, online, self.now()) is None:
                return False
            await self._finish(room)
            return True

    # --- timers and housekeeping --------------------------------------------

    async def tick(self, code: str) -> bool:
        """Advance one room if its round is overdue. Safe to call from any process."""
        async with self._store.lock(code):
            room = await self._store.load(code)
            if room is None:
                return False
            if not engine.tick(room, self.now()):
                return False
            await self._finish(room)
            return True

    async def tick_due(self) -> int:
        """Advance every room whose deadline has passed; return how many moved."""
        moved = 0
        for code in await self._store.due_rooms(self.now()):
            if await self.tick(code):
                moved += 1
        return moved

    async def delete_idle(self, idle_seconds: int) -> int:
        """Delete rooms with no activity for ``idle_seconds``; return how many."""
        stale = await self._store.stale_rooms(self.now() - idle_seconds)
        for code in stale:
            await self._store.delete(code)
        return len(stale)

    # --- internals -----------------------------------------------------------

    async def _mutate(
        self,
        code: str,
        action: Callable[[Room, float], object],
        actor_id: str | None = None,
        notify: bool = True,
    ) -> Room:
        """Lock, load, apply ``action``, save, notify. Engine errors leave state untouched."""
        code = code.upper()
        async with self._store.lock(code):
            room = await self.get_room(code)
            if actor_id is not None and room.player(actor_id) is None:
                raise ForbiddenError("not_in_room", "You are not part of this room.")
            action(room, self.now())
            await self._finish(room, notify)
            return room

    async def _finish(self, room: Room, notify: bool = True) -> None:
        """Archive for every signed-in player if the game just ended, then save and broadcast."""
        if (
            room.phase is Phase.RESULTS
            and any(p.account_id for p in room.players)
            and not room.archived
            and self._archiver is not None
        ):
            try:
                await self._archiver.archive(room)
                room.archived = True
            except Exception:  # never let a database problem break the game
                log.exception("archiving failed", extra={"room": room.code})
        await self._store.save(room)
        if notify:
            await self._publisher.room_changed(room.code)
