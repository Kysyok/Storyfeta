"""Domain models for a Storyfeta room.

These are plain dataclasses with no third-party dependencies so the whole game
engine can be unit-tested without Redis, FastAPI or a WebSocket. A ``Room`` is
serialised to JSON as a single document and stored in Redis (see
``app.storage.redis_store``).

Schemas
-------
Room
    code, host_id, phase, settings, players, stories, round_index,
    round_deadline, submitted_ids, drafts, reveal (story_index, lines_shown),
    archived, created_at, updated_at
Player
    id, nickname, is_host, account_id (set when the player was signed in)
Story
    index, starter_id, entries
StoryEntry
    round_index, author_id, text, reactions
Reaction
    emoji, player_ids  (one entry per emoji per line; a player counts once)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Phase(StrEnum):
    """Lifecycle of a room."""

    LOBBY = "lobby"
    WRITING = "writing"
    REVEAL = "reveal"
    RESULTS = "results"


@dataclass
class Settings:
    """Host-configurable game settings. ``turn_seconds=None`` disables the timer."""

    rounds: int = 5
    turn_seconds: int | None = 60


@dataclass
class Player:
    """A guest player. Identity is the id inside the signed session token."""

    id: str
    nickname: str
    is_host: bool = False
    account_id: str | None = None  # signed-in players get the game in their history


@dataclass
class Reaction:
    """Everyone who reacted to a line with one specific emoji."""

    emoji: str
    player_ids: list[str] = field(default_factory=list)


@dataclass
class StoryEntry:
    """One line of a story. Lines nobody wrote (timeouts) simply do not exist."""

    round_index: int
    author_id: str
    text: str
    reactions: list[Reaction] = field(default_factory=list)


@dataclass
class Story:
    """A story that travels around the circle. ``index`` equals its starter's seat."""

    index: int
    starter_id: str
    entries: list[StoryEntry] = field(default_factory=list)


@dataclass
class RevealCursor:
    """Which story is on screen and how many of its lines are uncovered."""

    story_index: int = 0
    lines_shown: int = 0


@dataclass
class Room:
    """Complete state of one game room."""

    code: str
    host_id: str
    settings: Settings = field(default_factory=Settings)
    phase: Phase = Phase.LOBBY
    players: list[Player] = field(default_factory=list)
    stories: list[Story] = field(default_factory=list)
    round_index: int = 0
    round_deadline: float | None = None
    submitted_ids: list[str] = field(default_factory=list)
    # Unsent lines typed this round (player id -> text); they count if time runs out.
    drafts: dict[str, str] = field(default_factory=dict)
    reveal: RevealCursor = field(default_factory=RevealCursor)
    archived: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    # --- lookups -----------------------------------------------------------

    def player(self, player_id: str) -> Player | None:
        """Return the player with this id, if they are in the room."""
        return next((p for p in self.players if p.id == player_id), None)

    def seat(self, player_id: str) -> int:
        """Return the player's position in the circle (join order)."""
        for i, p in enumerate(self.players):
            if p.id == player_id:
                return i
        raise KeyError(player_id)

    # --- (de)serialisation -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-safe dict."""
        data = asdict(self)
        data["phase"] = self.phase.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Room:
        """Rebuild a room from :meth:`to_dict` output."""
        stories = [
            Story(
                index=s["index"],
                starter_id=s["starter_id"],
                entries=[
                    StoryEntry(
                        round_index=e["round_index"],
                        author_id=e["author_id"],
                        text=e["text"],
                        reactions=[Reaction(**r) for r in e["reactions"]],
                    )
                    for e in s["entries"]
                ],
            )
            for s in data["stories"]
        ]
        return cls(
            code=data["code"],
            host_id=data["host_id"],
            settings=Settings(**data["settings"]),
            phase=Phase(data["phase"]),
            players=[Player(**p) for p in data["players"]],
            stories=stories,
            round_index=data["round_index"],
            round_deadline=data["round_deadline"],
            submitted_ids=list(data["submitted_ids"]),
            drafts=dict(data.get("drafts", {})),
            reveal=RevealCursor(**data["reveal"]),
            archived=data["archived"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )
