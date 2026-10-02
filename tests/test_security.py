"""Session tokens and password hashing."""

from __future__ import annotations

import pytest

from app.core import security

SECRET = "test-secret-with-enough-length-for-hs256"


def test_player_token_roundtrip():
    token = security.create_player_token(SECRET, "abc", "WXYZ", ttl=60)
    claims = security.read_player_token(SECRET, token)
    assert (claims.player_id, claims.room_code) == ("abc", "WXYZ")


def test_tampered_or_foreign_tokens_are_rejected():
    token = security.create_player_token(SECRET, "abc", "WXYZ", ttl=60)
    with pytest.raises(security.InvalidToken):
        security.read_player_token("another-secret-another-secret-123456", token)
    with pytest.raises(security.InvalidToken):
        security.read_player_token(SECRET, token[:-2] + "xx")
    with pytest.raises(security.InvalidToken):
        security.read_player_token(SECRET, "garbage")


def test_expired_token_is_rejected():
    token = security.create_player_token(SECRET, "abc", "WXYZ", ttl=-10)
    with pytest.raises(security.InvalidToken):
        security.read_player_token(SECRET, token)


def test_account_and_player_tokens_are_not_interchangeable():
    account = security.create_account_token(SECRET, "acc-1", ttl=60)
    assert security.read_account_token(SECRET, account) == "acc-1"
    with pytest.raises(security.InvalidToken):
        security.read_player_token(SECRET, account)
    player = security.create_player_token(SECRET, "abc", "WXYZ", ttl=60)
    with pytest.raises(security.InvalidToken):
        security.read_account_token(SECRET, player)


def test_password_hash_verifies_and_is_salted():
    first = security.hash_password("correct horse")
    second = security.hash_password("correct horse")
    assert first != second
    assert security.verify_password("correct horse", first)
    assert not security.verify_password("wrong", first)
    assert not security.verify_password("x", "not-a-hash")
