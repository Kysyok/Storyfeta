"""Signed session tokens (JWT) and password hashing.

* Guest players get a token naming their player id and room. It is stored in an
  HttpOnly cookie and authenticates the WebSocket and every action.
* Optional host accounts get a separate long-lived account token.
* Passwords use ``hashlib.scrypt`` from the standard library.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import uuid
from dataclasses import dataclass

import jwt

_ALGORITHM = "HS256"
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


class InvalidToken(Exception):
    """The token is missing, malformed, expired or signed with another key."""


@dataclass(frozen=True)
class PlayerClaims:
    """What a player token proves: who you are and which room it is for."""

    player_id: str
    room_code: str


def new_player_id() -> str:
    """Random, unguessable player id."""
    return uuid.uuid4().hex


def _encode(payload: dict[str, object], secret: str, ttl: int) -> str:
    now = int(time.time())
    return jwt.encode({**payload, "iat": now, "exp": now + ttl}, secret, _ALGORITHM)


def _decode(token: str, secret: str, expected_type: str) -> dict[str, object]:
    try:
        data = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
    if data.get("typ") != expected_type:
        raise InvalidToken("wrong token type")
    return data


def create_player_token(secret: str, player_id: str, room_code: str, ttl: int) -> str:
    """Sign a guest-player session token."""
    return _encode({"typ": "player", "sub": player_id, "room": room_code}, secret, ttl)


def read_player_token(secret: str, token: str) -> PlayerClaims:
    """Verify a guest-player token and return its claims."""
    data = _decode(token, secret, "player")
    return PlayerClaims(player_id=str(data["sub"]), room_code=str(data["room"]))


def create_account_token(secret: str, account_id: str, ttl: int) -> str:
    """Sign a host-account token."""
    return _encode({"typ": "account", "sub": account_id}, secret, ttl)


def read_account_token(secret: str, token: str) -> str:
    """Verify an account token and return the account id."""
    return str(_decode(token, secret, "account")["sub"])


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash: ``scrypt$n$r$p$salt$hash``."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against :func:`hash_password` output."""
    try:
        _, n, r, p, salt_b64, hash_b64 = stored.split("$")
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)
