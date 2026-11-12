"""Passwords (Argon2id), user access tokens (HS256 JWT) and machine tokens."""

from __future__ import annotations

import hmac
import time
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()           # Argon2id with the library's recommended parameters


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("cardforge-dummy-password")


def burn_verify_time(password: str) -> None:
    """Login with an unknown user: spend the same time as a real check, so response
    times do not reveal which usernames exist."""
    verify_password(_dummy_hash(), password)


def issue_token(user_id: int, username: str, secret: str, ttl_s: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    claims = {"sub": str(user_id), "username": username, "typ": "access",
              "iat": int(now), "exp": int(now + ttl_s)}
    return jwt.encode(claims, secret, algorithm="HS256")


def read_token(token: str, secret: str) -> int | None:
    """The user id in a valid, unexpired access token, else None."""
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["sub", "exp", "iat"]})
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != "access":
        return None
    try:
        return int(claims["sub"])
    except (TypeError, ValueError):
        return None


def machine_token_ok(token: str | None, allowed: list[str]) -> bool:
    if not token:
        return False
    return any(hmac.compare_digest(token.encode(), a.encode()) for a in allowed)


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None
