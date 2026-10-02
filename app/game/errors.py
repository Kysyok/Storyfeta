"""Errors raised by the game engine. The transport layer maps them to HTTP/WS errors."""

from __future__ import annotations


class GameError(Exception):
    """A rule of the game was broken. ``code`` is a stable machine-readable id."""

    status_code = 400

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(GameError):
    """The room (or something in it) does not exist."""

    status_code = 404


class ForbiddenError(GameError):
    """The caller is not allowed to do this (e.g. a non-host starting the game)."""

    status_code = 403
