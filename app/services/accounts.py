"""Optional host accounts: sign-up, sign-in, saved defaults and the archive.

CRUD over the two PostgreSQL entities:
    Account       create (register), read (get), update (nickname, defaults), delete
    ArchivedGame  create (ArchiveService.archive), read (list/get), update (title), delete
"""

from __future__ import annotations

import asyncio
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import security
from app.game import engine
from app.game.errors import GameError, NotFoundError
from app.models.domain import Settings
from app.models.orm import Account, ArchivedGame


class AccountService:
    """Everything a signed-in host can do. Guests never touch this."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], rules: engine.Rules) -> None:
        self._sessions = sessions
        self._rules = rules

    def _default_nickname(self, email: str) -> str:
        """First nickname when none is given: the part of the email before the @."""
        name = re.sub(r"[^\w .-]", "", email.split("@")[0])[: self._rules.max_nickname_length]
        return name.strip() or "Player"

    async def register(self, email: str, password: str, nickname: str | None = None) -> Account:
        """Create an account; emails are unique (case-insensitive)."""
        chosen = (
            engine.normalize_nickname(nickname, self._rules)
            if nickname and nickname.strip()
            else self._default_nickname(email)
        )
        # scrypt is CPU-bound: keep it off the event loop.
        password_hash = await asyncio.to_thread(security.hash_password, password)
        account = Account(
            email=email.strip().lower(), password_hash=password_hash, nickname=chosen
        )
        async with self._sessions() as session:
            session.add(account)
            try:
                await session.commit()
            except IntegrityError:
                raise GameError(
                    "email_taken", "An account with that email already exists."
                ) from None
        return account

    async def authenticate(self, email: str, password: str) -> Account:
        """Return the account if the credentials match, else raise."""
        async with self._sessions() as session:
            account = await session.scalar(
                select(Account).where(Account.email == email.strip().lower())
            )
        ok = account is not None and await asyncio.to_thread(
            security.verify_password, password, account.password_hash
        )
        if not ok or account is None:
            raise GameError("bad_credentials", "Email or password is wrong.")
        return account

    async def get(self, account_id: str) -> Account | None:
        """Look an account up by id (as found in the account token)."""
        async with self._sessions() as session:
            return await session.get(Account, uuid.UUID(account_id))

    async def update_nickname(self, account_id: str, nickname: str) -> Account:
        """Change the nickname that is used automatically in every room."""
        clean = engine.normalize_nickname(nickname, self._rules)
        async with self._sessions() as session:
            account = await session.get(Account, uuid.UUID(account_id))
            if account is None:
                raise NotFoundError("no_such_account", "Account not found.")
            account.nickname = clean
            await session.commit()
            return account

    async def update_defaults(self, account_id: str, settings: Settings) -> Account:
        """Save the host's preferred rounds/timer for new rooms."""
        engine.validate_settings(settings, self._rules)
        async with self._sessions() as session:
            account = await session.get(Account, uuid.UUID(account_id))
            if account is None:
                raise NotFoundError("no_such_account", "Account not found.")
            account.default_rounds = settings.rounds
            account.default_turn_seconds = settings.turn_seconds
            await session.commit()
            return account

    async def list_games(self, account_id: str, limit: int = 50) -> list[ArchivedGame]:
        """The host's finished games, newest first."""
        async with self._sessions() as session:
            result = await session.scalars(
                select(ArchivedGame)
                .where(ArchivedGame.account_id == uuid.UUID(account_id))
                .order_by(ArchivedGame.finished_at.desc())
                .limit(limit)
            )
            return list(result)

    async def delete_account(self, account_id: str) -> None:
        """Delete the account; its archived games go with it (ON DELETE CASCADE)."""
        async with self._sessions() as session:
            result = await session.execute(
                delete(Account).where(Account.id == uuid.UUID(account_id))
            )
            await session.commit()
        if result.rowcount == 0:
            raise NotFoundError("no_such_account", "Account not found.")

    @staticmethod
    async def _owned_game(session: AsyncSession, account_id: str, game_id: str) -> ArchivedGame:
        """Load a game, only if it belongs to this account."""
        try:
            key = uuid.UUID(game_id)
        except ValueError:
            raise NotFoundError("no_such_game", "That game is not in your archive.") from None
        game = await session.get(ArchivedGame, key)
        if game is None or str(game.account_id) != account_id:
            raise NotFoundError("no_such_game", "That game is not in your archive.")
        return game

    async def get_game(self, account_id: str, game_id: str) -> ArchivedGame:
        """One archived game, only if it belongs to this account."""
        async with self._sessions() as session:
            return await self._owned_game(session, account_id, game_id)

    async def rename_game(self, account_id: str, game_id: str, title: str | None) -> ArchivedGame:
        """Give a saved game a title; an empty title shows the room code again."""
        clean = " ".join((title or "").split()) or None
        async with self._sessions() as session:
            game = await self._owned_game(session, account_id, game_id)
            game.title = clean
            await session.commit()
            return game

    async def delete_game(self, account_id: str, game_id: str) -> None:
        """Remove a game from this account's history (other players keep their copies)."""
        async with self._sessions() as session:
            game = await self._owned_game(session, account_id, game_id)
            await session.delete(game)
            await session.commit()
