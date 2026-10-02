"""Round transitions, timeout handling and reaction counting."""

from __future__ import annotations

import pytest

from app.game import engine
from app.game.errors import ForbiddenError, GameError
from app.models.domain import Phase, Room, Settings
from tests.helpers import NOW, everyone_writes, lobby, started


# --- lobby -----------------------------------------------------------------


def test_nicknames_are_unique_ignoring_case():
    room = lobby(["Ann"])
    with pytest.raises(GameError) as err:
        engine.add_player(room, "p9", "  ann ", NOW)
    assert err.value.code == "nickname_taken"


def test_cannot_join_after_start():
    room = started(["Ann", "Bob"])
    with pytest.raises(GameError) as err:
        engine.add_player(room, "p9", "Cy", NOW)
    assert err.value.code == "game_started"


def test_settings_adjustable_in_lobby_by_host_only():
    room = lobby(["Ann", "Bob"])
    engine.update_settings(room, "p0", Settings(rounds=7, turn_seconds=None), NOW)
    assert room.settings == Settings(rounds=7, turn_seconds=None)
    with pytest.raises(ForbiddenError):
        engine.update_settings(room, "p1", Settings(rounds=4, turn_seconds=30), NOW)


def test_settings_locked_after_start():
    room = started(["Ann", "Bob"])
    with pytest.raises(GameError):
        engine.update_settings(room, "p0", Settings(rounds=4, turn_seconds=30), NOW)


def test_invalid_settings_rejected():
    room = lobby(["Ann", "Bob"])
    for bad in (Settings(rounds=1), Settings(rounds=99), Settings(turn_seconds=1)):
        with pytest.raises(GameError):
            engine.update_settings(room, "p0", bad, NOW)


def test_start_needs_host_and_enough_players():
    room = lobby(["Ann"])
    with pytest.raises(GameError) as err:
        engine.start_game(room, "p0", NOW)
    assert err.value.code == "not_enough_players"
    room = lobby(["Ann", "Bob"])
    with pytest.raises(ForbiddenError):
        engine.start_game(room, "p1", NOW)


# --- round transitions -----------------------------------------------------


def test_start_creates_one_story_per_player_and_sets_deadline():
    room = started(["Ann", "Bob", "Cy"], turn=30)
    assert room.phase is Phase.WRITING
    assert [s.starter_id for s in room.stories] == ["p0", "p1", "p2"]
    assert room.round_deadline == NOW + 30


def test_no_deadline_when_timer_disabled():
    room = started(["Ann", "Bob"], turn=None)
    assert room.round_deadline is None
    assert not engine.round_is_overdue(room, NOW + 10**6)


def test_round_zero_players_see_nothing():
    room = started(["Ann", "Bob", "Cy"])
    assert all(engine.prompt_for(room, p.id) is None for p in room.players)


def test_stories_rotate_and_players_see_only_previous_line():
    room = started(["Ann", "Bob", "Cy"], rounds=3)
    everyone_writes(room, NOW + 1)
    assert room.round_index == 1
    # Round 1: seat p continues story (p - 1) mod 3, i.e. the left neighbour's.
    assert engine.prompt_for(room, "p1") == "line r0 by Ann"
    assert engine.prompt_for(room, "p2") == "line r0 by Bob"
    assert engine.prompt_for(room, "p0") == "line r0 by Cy"
    everyone_writes(room, NOW + 2)
    # Only the newest line is shown, never the whole story.
    assert engine.prompt_for(room, "p2") == "line r1 by Bob"


def test_round_advances_only_when_everyone_submitted():
    room = started(["Ann", "Bob"])
    assert engine.submit_line(room, "p0", "hello", NOW + 1) is False
    assert room.round_index == 0
    assert engine.submit_line(room, "p1", "world", NOW + 2) is True
    assert room.round_index == 1
    assert room.submitted_ids == []
    assert room.round_deadline == NOW + 2 + 60


def test_last_round_moves_to_reveal():
    room = started(["Ann", "Bob"], rounds=2)
    everyone_writes(room, NOW + 1)
    everyone_writes(room, NOW + 2)
    assert room.phase is Phase.REVEAL
    assert room.round_deadline is None
    assert room.reveal.lines_shown == 0
    assert all(len(s.entries) == 2 for s in room.stories)


def test_rounds_may_exceed_player_count():
    room = started(["Ann", "Bob"], rounds=5)
    for i in range(5):
        everyone_writes(room, NOW + i)
    assert room.phase is Phase.REVEAL
    assert all(len(s.entries) == 5 for s in room.stories)


def test_double_submit_and_empty_and_long_lines_rejected():
    room = started(["Ann", "Bob"])
    engine.submit_line(room, "p0", "one", NOW)
    with pytest.raises(GameError) as err:
        engine.submit_line(room, "p0", "again", NOW)
    assert err.value.code == "already_submitted"
    for text, code in (("   \n ", "line_empty"), ("x" * 500, "line_too_long")):
        with pytest.raises(GameError) as err:
            engine.submit_line(room, "p1", text, NOW)
        assert err.value.code == code
    assert "p1" not in room.submitted_ids


def test_line_whitespace_is_collapsed():
    room = started(["Ann", "Bob"])
    engine.submit_line(room, "p0", "  a \n\n b\t c ", NOW)
    assert room.stories[0].entries[0].text == "a b c"


# --- timeouts --------------------------------------------------------------


def test_tick_does_nothing_before_deadline():
    room = started(["Ann", "Bob"], turn=30)
    assert engine.tick(room, NOW + 29) is False
    assert room.round_index == 0


def test_timeout_advances_without_blocking_the_group():
    room = started(["Ann", "Bob", "Cy"], turn=30)
    engine.submit_line(room, "p0", "Ann opens", NOW + 5)
    engine.submit_line(room, "p1", "Bob opens", NOW + 6)
    # Cy never writes.
    assert engine.tick(room, NOW + 30) is True
    assert room.round_index == 1
    assert len(room.stories[2].entries) == 0
    # Cy's story is empty, so whoever continues it (Bob, seat 1) sees nothing.
    assert engine.prompt_for(room, "p1") == "Ann opens"
    assert engine.prompt_for(room, "p0") is None


def test_next_player_sees_last_line_actually_written_after_a_skip():
    room = started(["Ann", "Bob", "Cy"], rounds=4, turn=30)
    everyone_writes(room, NOW + 1)  # round 0: all write
    # Round 1: Bob does not write on Ann's story.
    engine.submit_line(room, "p0", "Ann r1", NOW + 2)
    engine.submit_line(room, "p2", "Cy r1", NOW + 3)
    engine.tick(room, NOW + 1 + 30)
    assert room.round_index == 2
    # Round 2: Cy continues Ann's story (story 0). Bob skipped round 1 on it,
    # wait -- Bob wrote story 0 in round 1 (seat 1, (1-1)%3 = 0). He skipped it.
    # So Cy sees the last real line on story 0: Ann's opener.
    assert engine.prompt_for(room, "p2") == "line r0 by Ann"


def test_tick_is_idempotent_for_the_same_deadline():
    room = started(["Ann", "Bob"], turn=30)
    assert engine.tick(room, NOW + 31) is True
    # The new round has a fresh deadline, so a duplicate tick is a no-op.
    assert engine.tick(room, NOW + 31) is False
    assert room.round_index == 1


def test_late_submission_after_grace_is_rejected():
    room = started(["Ann", "Bob"], turn=30)
    with pytest.raises(GameError) as err:
        engine.submit_line(room, "p0", "too late", NOW + 30 + 5)
    assert err.value.code == "time_up"
    # Within the grace period it is still accepted.
    engine.submit_line(room, "p0", "just in time", NOW + 30 + 1)


def test_everyone_timing_out_all_game_ends_in_results():
    room = started(["Ann", "Bob"], rounds=2, turn=10)
    engine.tick(room, NOW + 10)
    engine.tick(room, NOW + 20)
    assert room.phase is Phase.RESULTS


def test_passed_on_line_can_be_taken_back_edited_and_passed_on_again():
    room = started(["Ann", "Bob", "Cy"], turn=None)
    engine.submit_line(room, "p0", "first draft", NOW)
    assert engine.submitted_text(room, "p0") == "first draft"
    engine.retract_line(room, "p0", NOW)
    assert room.stories[0].entries == [] and "p0" not in room.submitted_ids
    assert engine.submitted_text(room, "p0") is None
    engine.submit_line(room, "p0", "second draft", NOW)
    assert [e.text for e in room.stories[0].entries] == ["second draft"]


def test_round_starts_only_when_everyone_has_a_line_passed_on():
    room = started(["Ann", "Bob"], turn=None)
    engine.submit_line(room, "p0", "a", NOW)
    engine.submit_line(room, "p1", "b", NOW)  # last one locks the round in
    assert room.round_index == 1
    # p1 changes their mind in the new round: nothing to take back yet.
    with pytest.raises(GameError) as err:
        engine.retract_line(room, "p1", NOW)
    assert err.value.code == "not_submitted"


def test_taking_a_line_back_blocks_the_round_until_it_is_passed_on_again():
    room = started(["Ann", "Bob"], turn=None)
    engine.submit_line(room, "p0", "a", NOW)
    engine.retract_line(room, "p0", NOW)
    engine.submit_line(room, "p1", "b", NOW)
    assert room.round_index == 0  # Ann is still editing
    engine.submit_line(room, "p0", "a2", NOW)
    assert room.round_index == 1


def test_retract_only_touches_the_players_own_line():
    room = started(["Ann", "Bob", "Cy"], rounds=3, turn=None)
    everyone_writes(room, NOW)
    engine.submit_line(room, "p0", "Ann r1", NOW)
    engine.submit_line(room, "p1", "Bob r1", NOW)
    engine.retract_line(room, "p0", NOW)
    texts = sorted(e.text for s in room.stories for e in s.entries if e.round_index == 1)
    assert texts == ["Bob r1"]
    assert len([e for s in room.stories for e in s.entries if e.round_index == 0]) == 3


def test_retract_needs_writing_phase_a_player_and_open_timer():
    with pytest.raises(GameError) as err:
        engine.retract_line(lobby(["Ann", "Bob"]), "p0", NOW)
    assert err.value.code == "not_writing"
    room = started(["Ann", "Bob"], turn=30)
    with pytest.raises(GameError) as err:
        engine.retract_line(room, "stranger", NOW)
    assert err.value.code == "no_such_player"
    engine.submit_line(room, "p0", "a", NOW)
    with pytest.raises(GameError) as err:
        engine.retract_line(room, "p0", NOW + 40)
    assert err.value.code == "time_up"


def test_a_locked_line_survives_the_timeout_and_a_silent_player_leaves_no_line():
    room = started(["Ann", "Bob"], turn=30)
    engine.submit_line(room, "p0", "locked in", NOW)
    engine.tick(room, NOW + 30)  # Bob never typed anything
    assert room.round_index == 1
    assert room.stories[0].entries[0].text == "locked in"
    assert room.stories[1].entries == []


def test_a_typed_but_unsent_line_is_passed_on_when_time_runs_out():
    room = started(["Ann", "Bob"], turn=30)
    engine.submit_line(room, "p0", "locked in", NOW)
    assert engine.save_draft(room, "p1", "  half   a thought ", NOW + 29)
    engine.tick(room, NOW + 30)
    assert room.round_index == 1
    entry = room.stories[1].entries[0]
    assert (entry.text, entry.author_id, entry.round_index) == ("half a thought", "p1", 0)
    assert room.drafts == {}  # the next round starts clean


def test_drafts_follow_the_latest_text_and_an_emptied_draft_is_forgotten():
    room = started(["Ann", "Bob"], turn=30)
    engine.save_draft(room, "p1", "first", NOW)
    engine.save_draft(room, "p1", "second", NOW + 1)
    assert room.drafts == {"p1": "second"}
    engine.save_draft(room, "p1", "   ", NOW + 2)
    engine.tick(room, NOW + 30)
    assert room.stories[1].entries == []


def test_drafts_are_ignored_once_they_no_longer_apply():
    room = started(["Ann", "Bob"], turn=30)
    engine.submit_line(room, "p0", "done", NOW)
    assert not engine.save_draft(room, "p0", "too late, already passed on", NOW + 1)
    assert not engine.save_draft(room, "p1", "after the deadline", NOW + 40)
    assert not engine.save_draft(room, "nobody", "who?", NOW + 1)
    assert room.drafts == {}
    assert not engine.save_draft(lobby(["Ann", "Bob"]), "p0", "lobby", NOW)


def test_a_line_taken_back_to_edit_still_counts_if_time_runs_out():
    room = started(["Ann", "Bob"], turn=30)
    engine.submit_line(room, "p1", "original", NOW)
    engine.retract_line(room, "p1", NOW + 5)
    assert room.drafts == {"p1": "original"}
    engine.save_draft(room, "p1", "edited", NOW + 10)
    engine.tick(room, NOW + 30)
    assert [e.text for e in room.stories[1].entries] == ["edited"]


def test_submitting_replaces_the_draft():
    room = started(["Ann", "Bob"], turn=30)
    engine.save_draft(room, "p0", "draft", NOW)
    engine.submit_line(room, "p0", "final", NOW + 1)
    assert "p0" not in room.drafts
    engine.tick(room, NOW + 30)
    assert [e.text for e in room.stories[0].entries] == ["final"]


# --- reveal ----------------------------------------------------------------


def finished(names: list[str], rounds: int = 2) -> Room:
    room = started(names, rounds=rounds)
    for i in range(rounds):
        everyone_writes(room, NOW + i + 1)
    assert room.phase is Phase.REVEAL
    return room


def test_reveal_walks_lines_then_stories_then_results():
    room = finished(["Ann", "Bob"], rounds=2)
    seen = []
    while room.phase is Phase.REVEAL:
        seen.append((room.reveal.story_index, room.reveal.lines_shown))
        engine.reveal_next(room, "p0", NOW + 50)
    assert seen == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]
    assert room.phase is Phase.RESULTS


def test_reveal_skips_empty_stories():
    room = started(["Ann", "Bob", "Cy"], rounds=2, turn=10)
    engine.submit_line(room, "p2", "only Cy writes r0", NOW)
    engine.tick(room, NOW + 10)  # r0 ends; stories 0 and 1 are empty
    engine.tick(room, NOW + 20)  # r1 ends; nobody wrote
    assert room.phase is Phase.REVEAL
    assert room.reveal.story_index == 2
    for _ in range(2):
        engine.reveal_next(room, "p0", NOW + 30)
    assert room.phase is Phase.RESULTS


def test_reveal_next_is_host_only():
    room = finished(["Ann", "Bob"])
    with pytest.raises(ForbiddenError):
        engine.reveal_next(room, "p1", NOW)


# --- reactions -------------------------------------------------------------


def reveal_first_line(room: Room) -> str:
    engine.reveal_next(room, "p0", NOW)
    return engine.line_id(room.reveal.story_index, 0)


def test_reactions_count_once_per_player_and_toggle():
    room = finished(["Ann", "Bob", "Cy"])
    line = reveal_first_line(room)
    engine.toggle_reaction(room, "p0", line, "🔥", NOW)
    engine.toggle_reaction(room, "p1", line, "🔥", NOW)
    entry = room.stories[0].entries[0]
    assert engine.reaction_count(entry) == 2
    engine.toggle_reaction(room, "p1", line, "🔥", NOW)  # toggle off
    assert engine.reaction_count(entry) == 1
    engine.toggle_reaction(room, "p0", line, "🔥", NOW)
    assert entry.reactions == []


def test_cannot_react_to_hidden_or_unknown_lines():
    room = finished(["Ann", "Bob"])
    with pytest.raises(GameError) as err:
        engine.toggle_reaction(room, "p0", "0:0", "🔥", NOW)  # nothing uncovered yet
    assert err.value.code == "line_hidden"
    reveal_first_line(room)
    with pytest.raises(GameError):
        engine.toggle_reaction(room, "p0", "0:1", "🔥", NOW)  # second line still covered
    with pytest.raises(GameError):
        engine.toggle_reaction(room, "p0", "1:0", "🔥", NOW)  # other story
    with pytest.raises(GameError):
        engine.toggle_reaction(room, "p0", "junk", "🔥", NOW)


def test_only_allowed_emoji_and_only_during_reveal():
    room = finished(["Ann", "Bob"])
    line = reveal_first_line(room)
    with pytest.raises(GameError) as err:
        engine.toggle_reaction(room, "p0", line, "💩", NOW)
    assert err.value.code == "bad_emoji"
    writing = started(["Ann", "Bob"])
    with pytest.raises(GameError) as err:
        engine.toggle_reaction(writing, "p0", "0:0", "🔥", NOW)
    assert err.value.code == "not_revealing"


def test_room_roundtrips_through_dict():
    room = finished(["Ann", "Bob"])
    line = reveal_first_line(room)
    engine.toggle_reaction(room, "p1", line, "🔥", NOW)
    assert Room.from_dict(room.to_dict()) == room
