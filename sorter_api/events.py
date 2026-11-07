"""Pushes run events to every app connected on the WebSocket.

The run loop lives in a normal thread; WebSocket clients live on the asyncio
event loop. publish() can be called from any thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger(__name__)


class EventHub:
    def __init__(self, queue_size: int = 100) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._queue_size = queue_size

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        log.info("event %s", event.get("type"))
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fan_out, event)
        except RuntimeError:  # loop shutting down
            pass

    def _fan_out(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            if queue.full():          # slow client: drop its oldest event
                queue.get_nowait()
            queue.put_nowait(event)
