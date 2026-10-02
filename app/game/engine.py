"""Pure game rules for Storyfeta.

Every function here takes a :class:`~app.models.domain.Room`, mutates it and
returns nothing (or a small result). Nothing touches Redis, the clock or the
network: the current time is always passed in as ``now`` (epoch seconds). That
keeps round transitions, timeouts and reactions testable without a WebSocket.

How stories travel
------------------
Players sit in a circle in join order. Story ``s`` is started by the player in
seat ``s``. In round ``r`` the player in seat ``p`` continues story
``(p - r) mod n`` -- so in round 1 everybody continues their left neighbour's
story, in round 2 the one before that, and so on. A player only ever sees the
*last line that was actually written* on that story. If the previous player
timed out, no line exists for that round and the next player simply sees the
line before it (or nothing at all if the story is still empty).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from app.game.errors import ForbiddenError, GameError, NotFoundError
from app.models.domain import (
    Phase,
    Player,
    Reaction,
    RevealCursor,
    Room,
    Settings,
    Story,
    StoryEntry,
)


@dataclass(frozen=True)
class Rules:
    """Tunable limits. The app builds one from environment settings."""

    min_players: int = 2
    max_players: int = 12
    min_rounds: int = 2
    max_rounds: int = 20
    min_turn_seconds: int = 10
    max_turn_seconds: int = 600
    max_line_length: int = 280
    max_nickname_length: int = 20
    submit_grace_seconds: float = 1.5
    allowed_reactions: tuple[str, ...] = ("🔥",)


DEFAULT_RULES = Rules()

_WHITESPACE = re.compile(r"\s+")


# --- input normalisation ---------------------------------------------------


def normalize_nickname(raw: str, rules: Rules = DEFAULT_RULES) -> str:
    """Trim and collapse whitespace; reject empty or over-long nicknames."""
    nickname = _WHITESPACE.sub(" ", raw).strip()
    if not nickname:
        raise GameError("nickname_empty", "Enter a nickname to join.")
    if len(nickname) > rules.max_nickname_length:
        raise GameError(
            "nickname_too_long",
            f"Nicknames can be at most {rules.max_nickname_length} characters.",
        )
    return nickname


def normalize_line(raw: str, rules: Rules = DEFAULT_RULES) -> str:
    """Turn user input into a single clean line; reject empty or over-long text."""
    text = _WHITESPACE.sub(" ", raw).strip()
    if not text:
        raise GameError("line_empty", "Write something before passing it on.")
    if len(text) > rules.max_line_length:
        raise GameError(
            "line_too_long",
            f"Lines can be at most {rules.max_line_length} characters.",
        )
    return text


def validate_settings(settings: Settings, rules: Rules = DEFAULT_RULES) -> None:
    """Raise :class:`GameError` if the settings are outside the allowed range."""
    if not rules.min_rounds <= settings.rounds <= rules.max_rounds:
        raise GameError(
            "bad_rounds",
            f"Rounds must be between {rules.min_rounds} and {rules.max_rounds}.",
        )
    seconds = settings.turn_seconds
    if seconds is not None and not (
        rules.min_turn_seconds <= seconds <= rules.max_turn_seconds
    ):
        raise GameError(
            "bad_turn_seconds",
            f"Turn time must be between {rules.min_turn_seconds} and "
            f"{rules.max_turn_seconds} seconds, or turned off.",
        )


# --- lobby -----------------------------------------------------------------


def new_room(
    code: str,
    host_id: str,
    host_nickname: str,
    settings: Settings,
    now: float,
    account_id: str | None = None,
    rules: Rules = DEFAULT_RULES,
) -> Room:
    """Create a room in the lobby with the host as its first player."""
    validate_settings(settings, rules)
    host = Player(
        id=host_id,
        nickname=normalize_nickname(host_nickname, rules),
        is_host=True,
        account_id=account_id,
    )
    return Room(
        code=code,
        host_id=host_id,
        settings=settings,
        players=[host],
        created_at=now,
        updated_at=now,
    )


def add_player(
    room: Room,
    player_id: str,
    nickname: str,
    now: float,
    rules: Rules = DEFAULT_RULES,
    account_id: str | None = None,
) -> Player:
    """Add a guest to the lobby. Nicknames are unique (case-insensitive)."""
    if room.phase is not Phase.LOBBY:
        raise GameError("game_started", "This game has already started.")
    if len(room.players) >= rules.max_players:
        raise GameError("room_full", "This room is full.")
    clean = normalize_nickname(nickname, rules)
    if any(p.nickname.casefold() == clean.casefold() for p in room.players):
        raise GameError("nickname_taken", "Someone in this room already uses that name.")
    player = Player(id=player_id, nickname=clean, account_id=account_id)
    room.players.append(player)
    room.updated_at = now
    return player


def hand_off_host(room: Room, online: Collection[str], now: float) -> Player | None:
    """Make the first online player (in seat order) host if the host is gone.

    Returns the new host, or ``None`` if nothing changed (host still online, or
    nobody online to take over).
    """
    if room.host_id in online:
        return None
    heir = next((p for p in room.players if p.id in online), None)
    if heir is None:
        return None
    for p in room.players:
        p.is_host = p is heir
    room.host_id = heir.id
    room.updated_at = now
    return heir


def _require_host(room: Room, actor_id: str) -> None:
    if actor_id != room.host_id:
        raise ForbiddenError("host_only", "Only the host can do that.")


def update_settings(
    room: Room,
    actor_id: str,
    settings: Settings,
    now: float,
    rules: Rules = DEFAULT_RULES,
) -> None:
    """Host changes rounds/timer. Allowed only while players are joining."""
    _require_host(room, actor_id)
    if room.phase is not Phase.LOBBY:
        raise GameError("game_started", "Settings are locked once the game starts.")
    validate_settings(settings, rules)
    room.settings = settings
    room.updated_at = now


def start_game(
    room: Room, actor_id: str, now: float, rules: Rules = DEFAULT_RULES
) -> None:
    """Host starts round 0: one empty story per player."""
    _require_host(room, actor_id)
    if room.phase is not Phase.LOBBY:
        raise GameError("game_started", "This game has already started.")
    if len(room.players) < rules.min_players:
        raise GameError(
            "not_enough_players",
            f"You need at least {rules.min_players} players to start.",
        )
    room.stories = [Story(index=i, starter_id=p.id) for i, p in enumerate(room.players)]
    room.phase = Phase.WRITING
    _begin_round(room, 0, now)


# --- writing phase ---------------------------------------------------------


def story_index_for(seat: int, round_index: int, player_count: int) -> int:
    """Which story the player in ``seat`` writes on during ``round_index``."""
    return (seat - round_index) % player_count


def current_story_for(room: Room, player_id: str) -> Story:
    """The story ``player_id`` is writing on in the current round."""
    seat = room.seat(player_id)
    return room.stories[story_index_for(seat, room.round_index, len(room.players))]


def prompt_for(room: Room, player_id: str) -> str | None:
    """The last line actually written on the player's story, or ``None``.

    ``None`` means the story is still empty (round 0, or every earlier writer
    timed out), so the player may open it however they like.
    """
    story = current_story_for(room, player_id)
    return story.entries[-1].text if story.entries else None


def _begin_round(room: Room, round_index: int, now: float) -> None:
    room.round_index = round_index
    room.submitted_ids = []
    room.drafts = {}
    seconds = room.settings.turn_seconds
    room.round_deadline = now + seconds if seconds is not None else None
    room.updated_at = now


def submit_line(
    room: Room,
    player_id: str,
    raw_text: str,
    now: float,
    rules: Rules = DEFAULT_RULES,
) -> bool:
    """Record a player's line for this round.

    Returns ``True`` if this submission completed the round (and the game moved on).
    """
    if room.phase is not Phase.WRITING:
        raise GameError("not_writing", "Nobody is writing right now.")
    if room.player(player_id) is None:
        raise NotFoundError("no_such_player", "You are not in this room.")
    if player_id in room.submitted_ids:
        raise GameError("already_submitted", "You already passed this one on.")
    deadline = room.round_deadline
    if deadline is not None and now > deadline + rules.submit_grace_seconds:
        raise GameError("time_up", "Time is up for this round.")

    text = normalize_line(raw_text, rules)
    story = current_story_for(room, player_id)
    story.entries.append(
        StoryEntry(round_index=room.round_index, author_id=player_id, text=text)
    )
    room.submitted_ids.append(player_id)
    room.drafts.pop(player_id, None)
    room.updated_at = now

    if round_is_complete(room):
        advance(room, now)
        return True
    return False


def save_draft(
    room: Room, player_id: str, raw_text: str, now: float, rules: Rules = DEFAULT_RULES
) -> bool:
    """Remember the line a player is typing, so it still counts if time runs out.

    Drafts arrive in the background while the player types, so instead of raising
    they are simply ignored when they no longer apply. Returns whether one was stored.
    """
    if room.phase is not Phase.WRITING or room.player(player_id) is None:
        return False
    if player_id in room.submitted_ids:
        return False
    deadline = room.round_deadline
    if deadline is not None and now > deadline + rules.submit_grace_seconds:
        return False
    text = _WHITESPACE.sub(" ", raw_text).strip()[: rules.max_line_length]
    if text:
        room.drafts[player_id] = text
    else:
        room.drafts.pop(player_id, None)
    return True


def _commit_drafts(room: Room) -> None:
    """Pass on, as written, the unsent lines of players who ran out of time."""
    for player in room.players:
        text = room.drafts.get(player.id)
        if text and player.id not in room.submitted_ids:
            current_story_for(room, player.id).entries.append(
                StoryEntry(round_index=room.round_index, author_id=player.id, text=text)
            )
            room.submitted_ids.append(player.id)
    room.drafts = {}


def round_is_complete(room: Room) -> bool:
    """Everybody has submitted for the current round."""
    return len(room.submitted_ids) >= len(room.players)


def round_is_overdue(room: Room, now: float) -> bool:
    """The timer is on and has run out."""
    return room.round_deadline is not None and now >= room.round_deadline


def waiting_on(room: Room) -> list[Player]:
    """Players who have not yet submitted this round."""
    return [p for p in room.players if p.id not in room.submitted_ids]


def advance(room: Room, now: float) -> None:
    """Close the current round and open the next one (or the reveal).

    A line that was typed but not passed on in time is passed on as it is.
    Players who did not write at all simply leave no entry behind; the next
    writer on their story sees the last line that exists. Nobody is blocked.
    """
    _commit_drafts(room)
    if room.round_index + 1 >= room.settings.rounds:
        _begin_reveal(room, now)
    else:
        _begin_round(room, room.round_index + 1, now)


def tick(room: Room, now: float) -> bool:
    """Advance the room if its round is finished or overdue. Idempotent.

    Returns ``True`` if the state changed. Called by the timer loop, which may
    run in several processes at once -- callers hold the room lock.
    """
    if room.phase is not Phase.WRITING:
        return False
    if round_is_complete(room) or round_is_overdue(room, now):
        advance(room, now)
        return True
    return False


def submitted_text(room: Room, player_id: str) -> str | None:
    """The line this player has passed on in the current round, if any."""
    if player_id not in room.submitted_ids:
        return None
    story = current_story_for(room, player_id)
    entry = next(
        (
            e
            for e in story.entries
            if e.round_index == room.round_index and e.author_id == player_id
        ),
        None,
    )
    return entry.text if entry else None


def retract_line(
    room: Room, player_id: str, now: float, rules: Rules = DEFAULT_RULES
) -> None:
    """Take a passed-on line back so it can be edited; passing it on again locks it in.

    Only possible while the round is still open. The round advances the moment
    every player has a line passed on, so this can never undo a finished round.
    """
    if room.phase is not Phase.WRITING:
        raise GameError("not_writing", "Nobody is writing right now.")
    if room.player(player_id) is None:
        raise NotFoundError("no_such_player", "You are not in this room.")
    if player_id not in room.submitted_ids:
        raise GameError("not_submitted", "You haven't passed a line on yet.")
    deadline = room.round_deadline
    if deadline is not None and now > deadline + rules.submit_grace_seconds:
        raise GameError("time_up", "Time is up for this round.")

    story = current_story_for(room, player_id)
    mine = [
        e
        for e in story.entries
        if e.round_index == room.round_index and e.author_id == player_id
    ]
    story.entries[:] = [e for e in story.entries if e not in mine]
    if mine:  # while it is being edited, the line still counts if time runs out
        room.drafts[player_id] = mine[0].text
    room.submitted_ids.remove(player_id)
    room.updated_at = now


# --- reveal phase ----------------------------------------------------------


def _first_nonempty_story(room: Room, start: int = 0) -> int | None:
    for story in room.stories[start:]:
        if story.entries:
            return story.index
    return None


def _begin_reveal(room: Room, now: float) -> None:
    room.round_deadline = None
    room.submitted_ids = []
    room.updated_at = now
    first = _first_nonempty_story(room)
    if first is None:  # nobody wrote a single line
        room.phase = Phase.RESULTS
        return
    room.phase = Phase.REVEAL
    room.reveal = RevealCursor(story_index=first, lines_shown=0)


def reveal_next(room: Room, actor_id: str, now: float) -> None:
    """Host uncovers the next line, the next story, or finishes the reveal."""
    _require_host(room, actor_id)
    if room.phase is not Phase.REVEAL:
        raise GameError("not_revealing", "The reveal is not running.")
    cursor = room.reveal
    story = room.stories[cursor.story_index]
    room.updated_at = now
    if cursor.lines_shown < len(story.entries):
        cursor.lines_shown += 1
        return
    following = _first_nonempty_story(room, cursor.story_index + 1)
    if following is None:
        room.phase = Phase.RESULTS
    else:
        room.reveal = RevealCursor(story_index=following, lines_shown=0)


def line_id(story_index: int, round_index: int) -> str:
    """Stable id used by clients to address a line: ``"<story>:<round>"``."""
    return f"{story_index}:{round_index}"


def parse_line_id(value: str) -> tuple[int, int]:
    """Inverse of :func:`line_id`."""
    try:
        story_part, round_part = value.split(":")
        return int(story_part), int(round_part)
    except ValueError:
        raise GameError("bad_line", "That line does not exist.") from None


def toggle_reaction(
    room: Room,
    player_id: str,
    line: str,
    emoji: str,
    now: float,
    rules: Rules = DEFAULT_RULES,
) -> None:
    """Add the player's reaction to an uncovered line, or remove it if present.

    One player counts once per emoji per line. Only lines already uncovered in
    the story currently on screen can be reacted to.
    """
    if room.phase is not Phase.REVEAL:
        raise GameError("not_revealing", "Reactions are open during the reveal.")
    if room.player(player_id) is None:
        raise NotFoundError("no_such_player", "You are not in this room.")
    if emoji not in rules.allowed_reactions:
        raise GameError("bad_emoji", "That reaction is not available.")

    story_index, round_index = parse_line_id(line)
    if story_index != room.reveal.story_index:
        raise GameError("line_hidden", "That line is not on screen.")
    visible = room.stories[story_index].entries[: room.reveal.lines_shown]
    entry = next((e for e in visible if e.round_index == round_index), None)
    if entry is None:
        raise GameError("line_hidden", "That line is not uncovered yet.")

    reaction = next((r for r in entry.reactions if r.emoji == emoji), None)
    if reaction is None:
        entry.reactions.append(Reaction(emoji=emoji, player_ids=[player_id]))
    elif player_id in reaction.player_ids:
        reaction.player_ids.remove(player_id)
        if not reaction.player_ids:
            entry.reactions.remove(reaction)
    else:
        reaction.player_ids.append(player_id)
    room.updated_at = now


def reaction_count(entry: StoryEntry, emoji: str | None = None) -> int:
    """Number of reactions on a line, optionally for a single emoji."""
    return sum(
        len(r.player_ids) for r in entry.reactions if emoji is None or r.emoji == emoji
    )
