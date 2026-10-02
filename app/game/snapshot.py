"""Builds the personalised state a single player's screen is drawn from.

Pure: takes a room, the viewer and a few facts (who is online, the time) and
returns a :class:`~app.schemas.ws.RoomSnapshot`. The server never sends hidden
information: during writing a player only gets *their* prompt, and during the
reveal only lines that have been uncovered.
"""

from __future__ import annotations

from collections.abc import Collection

from app.game import engine
from app.game.engine import Rules
from app.game.results import top_lines
from app.models.domain import Phase, Player, Room
from app.schemas.ws import (
    LimitsView,
    LineView,
    PlayerView,
    ReactionView,
    ResultsView,
    RevealView,
    RoomSnapshot,
    SettingsView,
    TopLineView,
    WritingView,
)

TOP_LINES_SHOWN = 3


def _nickname(room: Room, player_id: str) -> str:
    player = room.player(player_id)
    return player.nickname if player else "someone who left"


def _player_view(room: Room, player: Player, online: Collection[str]) -> PlayerView:
    return PlayerView(
        id=player.id,
        nickname=player.nickname,
        is_host=player.is_host,
        online=player.id in online,
        submitted=player.id in room.submitted_ids,
    )


def _writing_view(room: Room, viewer_id: str) -> WritingView:
    return WritingView(
        round_index=room.round_index,
        total_rounds=room.settings.rounds,
        deadline=room.round_deadline,
        prompt=engine.prompt_for(room, viewer_id),
        has_submitted=viewer_id in room.submitted_ids,
        waiting_on=[p.nickname for p in engine.waiting_on(room)],
        my_text=engine.submitted_text(room, viewer_id),
        my_draft=room.drafts.get(viewer_id),
    )


def _reveal_view(room: Room, viewer_id: str) -> RevealView:
    story = room.stories[room.reveal.story_index]
    uncovered = story.entries[: room.reveal.lines_shown]
    non_empty = [s.index for s in room.stories if s.entries]
    lines = [
        LineView(
            line_id=engine.line_id(story.index, entry.round_index),
            author=_nickname(room, entry.author_id),
            text=entry.text,
            reactions=[
                ReactionView(
                    emoji=r.emoji,
                    count=len(r.player_ids),
                    mine=viewer_id in r.player_ids,
                )
                for r in entry.reactions
            ],
        )
        for entry in uncovered
    ]
    return RevealView(
        story_number=non_empty.index(story.index) + 1,
        story_count=len(non_empty),
        starter=_nickname(room, story.starter_id),
        lines=lines,
        total_lines=len(story.entries),
        is_last_story=story.index == non_empty[-1],
    )


def _results_view(room: Room, viewer: Player) -> ResultsView:
    return ResultsView(
        top_lines=[
            TopLineView(
                text=line.text,
                author=_nickname(room, line.author_id),
                story_starter=_nickname(room, room.stories[line.story_index].starter_id),
                reactions=line.reactions,
            )
            for line in top_lines(room, TOP_LINES_SHOWN)
        ],
        saved_to_archive=room.archived and viewer.account_id is not None,
    )


def build_snapshot(
    room: Room,
    viewer_id: str,
    online: Collection[str],
    now: float,
    rules: Rules = engine.DEFAULT_RULES,
) -> RoomSnapshot:
    """Assemble the snapshot for ``viewer_id`` (who must be in the room)."""
    viewer = room.player(viewer_id)
    if viewer is None:
        raise KeyError(viewer_id)
    return RoomSnapshot(
        code=room.code,
        phase=room.phase.value,
        server_time=now,
        you=_player_view(room, viewer, online),
        settings=SettingsView(
            rounds=room.settings.rounds, turn_seconds=room.settings.turn_seconds
        ),
        limits=LimitsView(
            min_rounds=rules.min_rounds,
            max_rounds=rules.max_rounds,
            min_turn_seconds=rules.min_turn_seconds,
            max_turn_seconds=rules.max_turn_seconds,
            max_line_length=rules.max_line_length,
            min_players=rules.min_players,
        ),
        reactions_allowed=list(rules.allowed_reactions),
        players=[_player_view(room, p, online) for p in room.players],
        writing=_writing_view(room, viewer_id) if room.phase is Phase.WRITING else None,
        reveal=_reveal_view(room, viewer_id) if room.phase is Phase.REVEAL else None,
        results=_results_view(room, viewer) if room.phase is Phase.RESULTS else None,
    )
