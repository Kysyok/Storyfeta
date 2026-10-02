"""End-of-game results, computed from a finished room.

Kept separate from the engine so richer results (per-player awards, funniest
story, ...) can be added later without touching game rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.game.engine import line_id, reaction_count
from app.models.domain import Room


@dataclass(frozen=True)
class ScoredLine:
    """A line together with its total number of reactions."""

    line_id: str
    story_index: int
    author_id: str
    text: str
    reactions: int


def scored_lines(room: Room) -> list[ScoredLine]:
    """All written lines with their reaction totals, in story/round order."""
    return [
        ScoredLine(
            line_id=line_id(story.index, entry.round_index),
            story_index=story.index,
            author_id=entry.author_id,
            text=entry.text,
            reactions=reaction_count(entry),
        )
        for story in room.stories
        for entry in story.entries
    ]


def top_lines(room: Room, limit: int = 3) -> list[ScoredLine]:
    """The most-reacted-to lines. Lines with no reactions never qualify.

    Ties keep story/round order, so the result is deterministic.
    """
    ranked = sorted(
        (line for line in scored_lines(room) if line.reactions > 0),
        key=lambda line: -line.reactions,
    )
    return ranked[:limit]
