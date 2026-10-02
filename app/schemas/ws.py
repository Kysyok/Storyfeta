"""WebSocket protocol: every message that crosses ``/ws/{code}`` is defined here.

Client -> server (``type`` is the discriminator)::

    set_settings  {rounds, turn_seconds|null}   host, lobby only
    start_game    {}                            host
    submit_line   {text}                        writing phase
    unsubmit_line {}                            writing phase; take your line back to edit it
    reveal_next   {}                            host, reveal phase
    react         {line_id, emoji}              reveal phase (toggles)
    ping          {}

Server -> client::

    state   {state: RoomSnapshot}   full, personalised view; sent on every change
    error   {code, message}         a rule was broken; the state is unchanged
    pong    {}
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


# --- server -> client: the snapshot -----------------------------------------


class PlayerView(BaseModel):
    """A player as shown in lists."""

    id: str
    nickname: str
    is_host: bool
    online: bool
    submitted: bool


class SettingsView(BaseModel):
    """Current game settings."""

    rounds: int
    turn_seconds: int | None


class LimitsView(BaseModel):
    """Bounds the settings form should respect."""

    min_rounds: int
    max_rounds: int
    min_turn_seconds: int
    max_turn_seconds: int
    max_line_length: int
    min_players: int


class WritingView(BaseModel):
    """What the viewer needs while stories are being written."""

    round_index: int
    total_rounds: int
    deadline: float | None
    prompt: str | None = Field(description="Last line written on the viewer's story.")
    has_submitted: bool
    waiting_on: list[str]
    my_text: str | None = Field(description="The viewer's passed-on line, if any.")
    my_draft: str | None = Field(
        default=None,
        description="The viewer's unsent line, kept on the server (survives a reload).",
    )


class ReactionView(BaseModel):
    """Reaction total for one emoji on one line."""

    emoji: str
    count: int
    mine: bool


class LineView(BaseModel):
    """An uncovered line in the reveal."""

    line_id: str
    author: str
    text: str
    reactions: list[ReactionView]


class RevealView(BaseModel):
    """The story currently on screen and its uncovered lines."""

    story_number: int
    story_count: int
    starter: str
    lines: list[LineView]
    total_lines: int
    is_last_story: bool


class TopLineView(BaseModel):
    """A line in the results ranking."""

    text: str
    author: str
    story_starter: str
    reactions: int


class ResultsView(BaseModel):
    """End-of-game summary."""

    top_lines: list[TopLineView]
    saved_to_archive: bool


class RoomSnapshot(BaseModel):
    """Everything one player's screen needs. Personalised per viewer."""

    code: str
    phase: Literal["lobby", "writing", "reveal", "results"]
    server_time: float
    you: PlayerView
    settings: SettingsView
    limits: LimitsView
    reactions_allowed: list[str]
    players: list[PlayerView]
    writing: WritingView | None = None
    reveal: RevealView | None = None
    results: ResultsView | None = None


class StateMessage(BaseModel):
    """Server pushes a fresh snapshot."""

    type: Literal["state"] = "state"
    state: RoomSnapshot


class ErrorMessage(BaseModel):
    """Server reports a rejected action."""

    type: Literal["error"] = "error"
    code: str
    message: str


class PongMessage(BaseModel):
    """Reply to ``ping``."""

    type: Literal["pong"] = "pong"


# --- client -> server -------------------------------------------------------


class SetSettings(BaseModel):
    """Host changes rounds / timer."""

    type: Literal["set_settings"]
    rounds: int
    turn_seconds: int | None = None


class StartGame(BaseModel):
    """Host starts the game."""

    type: Literal["start_game"]


class SubmitLine(BaseModel):
    """Player passes their line on."""

    type: Literal["submit_line"]
    text: str = Field(max_length=2000)


class SaveDraft(BaseModel):
    """The line the player is typing; passed on automatically if time runs out."""

    type: Literal["save_draft"]
    text: str = Field(max_length=2000)


class UnsubmitLine(BaseModel):
    """Player takes their passed-on line back to edit it."""

    type: Literal["unsubmit_line"]


class RevealNext(BaseModel):
    """Host uncovers the next line / story."""

    type: Literal["reveal_next"]


class React(BaseModel):
    """Player toggles an emoji on a line."""

    type: Literal["react"]
    line_id: str = Field(max_length=32)
    emoji: str = Field(max_length=16)


class Ping(BaseModel):
    """Keep-alive."""

    type: Literal["ping"]


ClientMessage = Annotated[
    SetSettings | StartGame | SubmitLine | SaveDraft | UnsubmitLine | RevealNext | React | Ping,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
