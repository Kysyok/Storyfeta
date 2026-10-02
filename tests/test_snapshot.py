"""What each player is allowed to see: personalised, never leaking hidden lines."""

from __future__ import annotations

from app.game import engine
from app.game.snapshot import build_snapshot
from tests.helpers import NOW, everyone_writes, started


def snap(room, viewer, online=("p0", "p1", "p2")):
    return build_snapshot(room, viewer, set(online), NOW, engine.DEFAULT_RULES)


def test_lobby_snapshot_lists_players_with_presence():
    from tests.helpers import lobby

    room = lobby(["Ann", "Bob"])
    s = snap(room, "p1", online=("p0",))
    assert s.phase == "lobby" and s.writing is None and s.reveal is None
    assert [(p.nickname, p.online) for p in s.players] == [("Ann", True), ("Bob", False)]
    assert s.you.nickname == "Bob" and not s.you.is_host


def test_each_writer_gets_only_their_own_prompt():
    room = started(["Ann", "Bob", "Cy"])
    everyone_writes(room, NOW + 1)
    prompts = {p: snap(room, p).writing.prompt for p in ("p0", "p1", "p2")}
    assert prompts == {"p0": "line r0 by Cy", "p1": "line r0 by Ann", "p2": "line r0 by Bob"}


def test_writing_snapshot_tracks_who_is_still_writing():
    room = started(["Ann", "Bob", "Cy"])
    engine.submit_line(room, "p1", "hi", NOW)
    w = snap(room, "p1").writing
    assert w.has_submitted and w.waiting_on == ["Ann", "Cy"]
    assert not snap(room, "p0").writing.has_submitted


def test_reveal_snapshot_hides_covered_lines_and_marks_own_reactions():
    room = started(["Ann", "Bob"], rounds=2)
    everyone_writes(room, NOW + 1)
    everyone_writes(room, NOW + 2)
    assert snap(room, "p0").reveal.lines == []
    engine.reveal_next(room, "p0", NOW)
    line_id = engine.line_id(0, 0)
    engine.toggle_reaction(room, "p1", line_id, "🔥", NOW)
    mine, theirs = snap(room, "p1").reveal, snap(room, "p0").reveal
    assert len(mine.lines) == 1 and mine.total_lines == 2
    assert mine.lines[0].reactions[0].count == 1
    assert mine.lines[0].reactions[0].mine and not theirs.lines[0].reactions[0].mine
    assert mine.starter == "Ann" and mine.story_number == 1 and mine.story_count == 2


def test_writing_snapshot_returns_own_locked_text_only_to_its_author():
    room = started(["Ann", "Bob", "Cy"], turn=None)
    engine.submit_line(room, "p1", "my locked line", NOW)
    assert snap(room, "p1").writing.my_text == "my locked line"
    assert snap(room, "p0").writing.my_text is None
    engine.retract_line(room, "p1", NOW)
    assert snap(room, "p1").writing.my_text is None
    assert not snap(room, "p1").writing.has_submitted


def test_saved_note_is_only_for_signed_in_viewers():
    from app.models.domain import Phase

    room = started(["Ann", "Bob"], rounds=2)
    room.players[0].account_id = "acct-ann"
    everyone_writes(room, NOW + 1)
    everyone_writes(room, NOW + 2)
    room.phase, room.archived = Phase.RESULTS, True
    assert snap(room, "p0").results.saved_to_archive is True
    assert snap(room, "p1").results.saved_to_archive is False


def test_a_draft_is_returned_only_to_its_author():
    room = started(["Ann", "Bob", "Cy"])
    engine.save_draft(room, "p1", "secret draft", NOW)
    assert snap(room, "p1").writing.my_draft == "secret draft"
    assert snap(room, "p0").writing.my_draft is None
