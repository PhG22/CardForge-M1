"""Message framing over BLE.

A BLE write or notification carries at most MTU - 3 bytes (20 bytes with the
default MTU of 23). Messages are UTF-8 JSON objects ending with a newline, split
into chunks of any size. JSON never contains a raw newline (json.dumps escapes
it), so the newline always marks the end of a message.
"""

from __future__ import annotations

import time
from typing import Callable, Iterator, List

MAX_MESSAGE_BYTES = 1024
NEWLINE = 0x0A


class FrameDecoder:
    """Joins incoming chunks into complete messages.

    A partial message that gets no new bytes for `stale_after` seconds is
    dropped: it belongs to a phone that disconnected in the middle of a write.
    A message longer than `max_bytes` is dropped too, and reported as `None`
    so the caller can answer TOO_LONG.
    """

    def __init__(
        self,
        max_bytes: int = MAX_MESSAGE_BYTES,
        stale_after: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_bytes
        self._stale_after = stale_after
        self._clock = clock
        self._buffer = bytearray()
        self._last_chunk_at = 0.0
        self._overflow = False

    def feed(self, chunk: bytes) -> List[bytes | None]:
        now = self._clock()
        if self._buffer and now - self._last_chunk_at > self._stale_after:
            self._buffer.clear()
            self._overflow = False
        self._last_chunk_at = now

        messages: List[bytes | None] = []
        for byte in chunk:
            if byte == NEWLINE:
                if self._overflow:
                    messages.append(None)
                elif self._buffer.strip():
                    messages.append(bytes(self._buffer))
                self._buffer.clear()
                self._overflow = False
            elif not self._overflow:
                self._buffer.append(byte)
                if len(self._buffer) > self._max:
                    self._buffer.clear()
                    self._overflow = True
        return messages


def split_frames(message: bytes, chunk_size: int) -> Iterator[bytes]:
    """Adds the newline and yields chunks of at most `chunk_size` bytes."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    data = message + b"\n"
    for start in range(0, len(data), chunk_size):
        yield data[start : start + chunk_size]
