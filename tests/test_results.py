"""Most-reacted-to lines."""

from __future__ import annotations

from app.game import engine
from app.game.results import top_lines
from app.models.domain import Phase
from tests.helpers import NOW, everyone_writes, started


def test_top_lines_orders_by_reactions_and_ignores_zero():
    room = started(["Ann", "Bob", "Cy"], rounds=2)
    everyone_writes(room, NOW + 1)
    everyone_writes(room, NOW + 2)
    # Walk the reveal, reacting as we go: story 0 line 0 gets 3, story 1 line 1 gets 1.
    while room.phase is Phase.REVEAL:
        engine.reveal_next(room, "p0", NOW)
        if room.phase is not Phase.REVEAL or room.reveal.lines_shown == 0:
            continue
        line = engine.line_id(
            room.reveal.story_index,
            room.reveal.lines_shown - 1,
        )
        s, r = room.reveal.story_index, room.reveal.lines_shown - 1
        if (s, r) == (0, 0):
            for pid in ("p0", "p1", "p2"):
                engine.toggle_reaction(room, pid, line, "🔥", NOW)
        if (s, r) == (1, 1):
            engine.toggle_reaction(room, "p2", line, "🔥", NOW)
    best = top_lines(room)
    assert [(line.line_id, line.reactions) for line in best] == [("0:0", 3), ("1:1", 1)]


def test_top_lines_empty_without_reactions():
    room = started(["Ann", "Bob"], rounds=2)
    everyone_writes(room, NOW + 1)
    everyone_writes(room, NOW + 2)
    assert top_lines(room) == []
