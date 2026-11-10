"""Machine password and short-lived setup sessions.

The password is stored as a PBKDF2-SHA256 hash, never in plain text. After
`max_attempts` wrong passwords in a row the service refuses every attempt for
`lockout_seconds`, so the password can't be guessed quickly over Bluetooth.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

ITERATIONS = 120_000  # about 1 s on a Raspberry Pi 3; checks run off the event loop
SESSION_SECONDS = 600
MAX_SESSIONS = 4


def hash_password(password: str, *, iterations: int = ITERATIONS, salt: Optional[bytes] = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"pbkdf2_sha256${iterations}${b64(salt)}${b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@dataclass
class AuthResult:
    ok: bool
    session: Optional[str] = None
    error: Optional[str] = None  # WRONG_PASSWORD or LOCKED
    attempts_left: Optional[int] = None
    retry_after: Optional[int] = None


class AuthManager:
    def __init__(
        self,
        password_hash: str,
        *,
        max_attempts: int = 5,
        lockout_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hash = password_hash
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds
        self._clock = clock
        self._failures = 0
        self._locked_until = 0.0
        self._sessions: Dict[str, float] = {}  # token -> expiry

    def check_lock(self) -> Optional[AuthResult]:
        """A LOCKED result while locked out, else None. Cheap: no hashing."""
        remaining = self._locked_until - self._clock()
        if remaining > 0:
            return AuthResult(ok=False, error="LOCKED", retry_after=int(remaining) + 1)
        return None

    def login(self, password: str) -> AuthResult:
        """Checks the password (slow, run it in a thread) and opens a session."""
        locked = self.check_lock()
        if locked:
            return locked
        if not verify_password(password, self._hash):
            self._failures += 1
            if self._failures >= self._max_attempts:
                self._failures = 0
                self._locked_until = self._clock() + self._lockout_seconds
                return AuthResult(ok=False, error="LOCKED", retry_after=self._lockout_seconds)
            return AuthResult(
                ok=False, error="WRONG_PASSWORD", attempts_left=self._max_attempts - self._failures
            )
        self._failures = 0
        return AuthResult(ok=True, session=self._open_session())

    def is_valid(self, token: object) -> bool:
        if not isinstance(token, str):
            return False
        self._drop_expired()
        return token in self._sessions

    def _open_session(self) -> str:
        self._drop_expired()
        while len(self._sessions) >= MAX_SESSIONS:
            oldest = min(self._sessions, key=self._sessions.__getitem__)
            del self._sessions[oldest]
        token = secrets.token_hex(16)
        self._sessions[token] = self._clock() + SESSION_SECONDS
        return token

    def _drop_expired(self) -> None:
        now = self._clock()
        for token in [t for t, expiry in self._sessions.items() if expiry <= now]:
            del self._sessions[token]
