"""RF38: wakes the app's waiting requests (GET /notifications?wait=...) as soon as a new
alert for their user is stored.

The alerts themselves live in PostgreSQL (table notifications); this only says "look
again". The server runs as one process, so keeping the waiters in memory is enough.
publish() may be called from any thread (the sync endpoints run in a thread pool).
"""

from __future__ import annotations

import asyncio
import threading


class NotificationBroker:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiters: dict[int, set[asyncio.Event]] = {}
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, user_id: int) -> asyncio.Event:
        """Register before reading the database, so an alert stored in between is not missed."""
        event = asyncio.Event()
        with self._lock:
            self._waiters.setdefault(user_id, set()).add(event)
        return event

    def unsubscribe(self, user_id: int, event: asyncio.Event) -> None:
        with self._lock:
            waiters = self._waiters.get(user_id)
            if waiters is not None:
                waiters.discard(event)
                if not waiters:
                    del self._waiters[user_id]

    async def wait(self, event: asyncio.Event, timeout_s: float) -> bool:
        """True when an alert for the user arrived within timeout_s."""
        try:
            await asyncio.wait_for(event.wait(), timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    def publish(self, user_id: int) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._wake, user_id)
        except RuntimeError:          # loop shutting down
            pass

    def waiting(self, user_id: int) -> int:
        with self._lock:
            return len(self._waiters.get(user_id, ()))

    def _wake(self, user_id: int) -> None:
        with self._lock:
            waiters = list(self._waiters.get(user_id, ()))
        for event in waiters:
            event.set()
