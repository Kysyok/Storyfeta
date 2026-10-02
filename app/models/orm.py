"""SQLAlchemy models for the optional PostgreSQL side: host accounts and the archive.

Active games never touch these tables; they live in Redis. A finished game
owned by a signed-in host is copied here once, as one JSON document.

Account
    id (uuid), email (unique), nickname, password_hash, default_rounds,
    default_turn_seconds (null = no timer), created_at
ArchivedGame
    id (uuid), account_id -> Account, room_code, title (optional), rounds, finished_at,
    payload (JSONB: players + stories with authors, text, reaction counts)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base; Alembic reads ``Base.metadata``."""


class Account(Base):
    """A host who chose to save their games."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(20))
    default_rounds: Mapped[int] = mapped_column(Integer, default=5)
    default_turn_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    games: Mapped[list[ArchivedGame]] = relationship(back_populates="account")


class ArchivedGame(Base):
    """A finished game, stored whole."""

    __tablename__ = "archived_games"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    room_code: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rounds: Mapped[int] = mapped_column(Integer)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)

    account: Mapped[Account] = relationship(back_populates="games")
