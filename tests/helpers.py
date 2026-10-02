"""Small builders shared by the engine tests."""

from __future__ import annotations

from app.game import engine
from app.models.domain import Room, Settings

NOW = 1_000.0


def lobby(names: list[str], rounds: int = 3, turn: int | None = 60) -> Room:
    """A lobby with one player per name; the first is the host (id ``p0``)."""
    room = engine.new_room(
        "ABCD", "p0", names[0], Settings(rounds=rounds, turn_seconds=turn), NOW
    )
    for i, name in enumerate(names[1:], start=1):
        engine.add_player(room, f"p{i}", name, NOW)
    return room


def started(names: list[str], rounds: int = 3, turn: int | None = 60) -> Room:
    """A room whose round 0 is running."""
    room = lobby(names, rounds, turn)
    engine.start_game(room, "p0", NOW)
    return room


def everyone_writes(room: Room, now: float, tag: str = "line") -> None:
    """All players submit one line for the current round."""
    r = room.round_index
    for p in list(room.players):
        engine.submit_line(room, p.id, f"{tag} r{r} by {p.nickname}", now)
