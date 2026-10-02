"""Turns a finished room into the JSON document stored in the archive."""

from __future__ import annotations

from typing import Any

from app.game.engine import reaction_count
from app.models.domain import Room


def build_archive_payload(room: Room) -> dict[str, Any]:
    """Nicknames (not ids) and reaction totals for every written line.

    Stories nobody wrote on are left out.
    """
    names = {p.id: p.nickname for p in room.players}
    return {
        "players": [p.nickname for p in room.players],
        "stories": [
            {
                "starter": names[story.starter_id],
                "lines": [
                    {
                        "author": names.get(e.author_id, "someone who left"),
                        "text": e.text,
                        "reactions": reaction_count(e),
                    }
                    for e in story.entries
                ],
            }
            for story in room.stories
            if story.entries
        ],
    }
