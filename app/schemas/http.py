"""Request and response bodies for the REST endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateRoomRequest(BaseModel):
    """Start a room. Rounds and timer are adjusted afterwards, in the lobby."""

    nickname: str | None = Field(
        default=None,
        max_length=64,
        description="Optional for signed-in hosts: their profile nickname is used.",
    )


class JoinRoomRequest(BaseModel):
    """Join an existing room."""

    nickname: str | None = Field(
        default=None,
        max_length=64,
        description="Optional for signed-in players: their profile nickname is used.",
    )


class RoomJoined(BaseModel):
    """Returned after creating or joining a room. The session cookie is set too."""

    code: str
    player_id: str
    url: str
    token: str = Field(description="Same token as the cookie, for non-browser clients.")


class RoomInfo(BaseModel):
    """Public facts about a room, for the join page."""

    code: str
    phase: str
    player_count: int
    joinable: bool


class ErrorBody(BaseModel):
    """Uniform error payload."""

    code: str
    message: str


class Credentials(BaseModel):
    """Register / log in."""

    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)


class Register(Credentials):
    """Create an account. The nickname is optional; it defaults to the email name."""

    nickname: str | None = Field(default=None, max_length=64)


class AccountView(BaseModel):
    """The signed-in host."""

    id: str
    email: str
    nickname: str
    default_rounds: int
    default_turn_seconds: int | None


class UpdateProfile(BaseModel):
    """Change the nickname used in every game."""

    nickname: str = Field(min_length=1, max_length=64)


class UpdateDefaults(BaseModel):
    """Host's saved defaults for new rooms."""

    default_rounds: int
    default_turn_seconds: int | None = None


class ArchiveItem(BaseModel):
    """One finished game in the host's archive list."""

    id: str
    room_code: str
    title: str | None
    rounds: int
    players: list[str]
    finished_at: datetime


class UpdateArchivedGame(BaseModel):
    """Rename a saved game. An empty or null title falls back to the room code."""

    title: str | None = Field(default=None, max_length=80)


class ArchivedLine(BaseModel):
    """A line of an archived story."""

    author: str
    text: str
    reactions: int


class ArchivedStory(BaseModel):
    """An archived story."""

    starter: str
    lines: list[ArchivedLine]


class ArchiveDetail(ArchiveItem):
    """A finished game in full."""

    stories: list[ArchivedStory]
