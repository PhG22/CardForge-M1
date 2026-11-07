"""Half-duplex RS-485 master running on the Raspberry Pi.

One transaction = one request frame and one reply frame. A lock makes sure only
one transaction is on the bus at a time, whichever thread asks for it (the run
loop, the stop endpoint, the hardware-status endpoint...).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Protocol

from .protocol import REPLY_START, REQUEST_START, FrameError, Reply, decode_frame, encode_frame

log = logging.getLogger(__name__)


class SerialPort(Protocol):
    """The part of pyserial's Serial the bus uses (the simulator implements it too)."""

    def write(self, data: bytes) -> int | None: ...
    def flush(self) -> None: ...
    def read(self, size: int = 1) -> bytes: ...
    @property
    def in_waiting(self) -> int: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


class DirectionPin(Protocol):
    """Drives the MAX485 DE/RE pins (tied together): high = transmit, low = receive."""

    def transmit(self) -> None: ...
    def receive(self) -> None: ...
    def close(self) -> None: ...


class GpioDirectionPin:
    """DE/RE on a Raspberry Pi GPIO, via gpiozero (works on Pi 3, 4 and 5)."""

    def __init__(self, bcm_pin: int) -> None:
        from gpiozero import DigitalOutputDevice  # imported here: only needed on the Pi

        self._dev = DigitalOutputDevice(bcm_pin, active_high=True, initial_value=False)

    def transmit(self) -> None:
        self._dev.on()

    def receive(self) -> None:
        self._dev.off()

    def close(self) -> None:
        self._dev.close()


class RtsDirectionPin:
    """DE/RE wired to the RTS pin of a USB-serial (TTL) adapter, for running the API on a laptop.

    On FTDI/CP2102/CH340 adapters the RTS pin is active-low: rts=True puts the pin LOW.
    So by default "transmit" sets rts=False (pin HIGH = DE on). invert=True swaps that.
    """

    def __init__(self, port, invert: bool = False) -> None:
        self._port = port
        self._invert = invert

    def transmit(self) -> None:
        self._port.rts = self._invert

    def receive(self) -> None:
        self._port.rts = not self._invert

    def close(self) -> None:
        pass


class BusError(Exception):
    """Base class for bus problems."""


class ModuleNotResponding(BusError):
    def __init__(self, addr: str, command: str, last_error: Exception | None) -> None:
        self.addr = addr
        self.command = command
        self.last_error = last_error
        super().__init__(f"module {addr} did not answer {command!r} ({last_error})")


class ModuleError(BusError):
    """The module answered ERR <code>."""

    def __init__(self, addr: str, command: str, code: str, fields: dict[str, str]) -> None:
        self.addr = addr
        self.command = command
        self.code = code
        self.fields = fields
        super().__init__(f"module {addr} rejected {command!r}: {code}")


class RS485Bus:
    def __init__(
        self,
        port: SerialPort,
        *,
        baudrate: int,
        direction_pin: DirectionPin | None = None,
        reply_timeout: float = 0.3,
        retries: int = 2,
        frame_gap: float = 0.002,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._de = direction_pin
        self._reply_timeout = reply_timeout
        self._retries = retries
        self._frame_gap = frame_gap
        self._last_rx = 0.0
        self._lock = threading.Lock()
        self._rx = bytearray()
        # Random first seq: after a Pi restart the Arduinos must not mistake the
        # first new request for a retry of the last one they saw.
        self._seq = random.randrange(256)
        if self._de is not None:
            self._de.receive()

    def close(self) -> None:
        with self._lock:
            self._port.close()
            if self._de is not None:
                self._de.close()

    def transact(self, addr: str, command: str, *args: object, retries: int | None = None) -> Reply:
        """Send one command and return the OK reply.

        Raises ModuleError if the module answers ERR, ModuleNotResponding if no
        valid reply arrives after all retries. Retries reuse the same seq, so the
        Arduino can recognise them and resend its reply instead of executing the
        command twice.
        """
        tokens = [command, *(str(a) for a in args)]
        retries = self._retries if retries is None else retries
        with self._lock:
            self._seq = (self._seq + 1) % 256
            seq = self._seq
            frame = encode_frame(REQUEST_START, addr, seq, tokens)
            last_error: Exception | None = None
            for attempt in range(retries + 1):
                if attempt:
                    log.warning("retry %d/%d %s %r (%s)", attempt, retries, addr, " ".join(tokens), last_error)
                # SoftwareSerial on the Arduinos needs a moment after its own reply
                # before it can catch the start bit of the next request
                wait = self._frame_gap - (time.monotonic() - self._last_rx)
                if wait > 0:
                    time.sleep(wait)
                self._port.reset_input_buffer()
                self._rx.clear()
                self._send(frame)
                try:
                    reply = self._await_reply(addr, seq)
                except (TimeoutError, FrameError) as exc:
                    last_error = exc
                    continue
                if not reply.ok:
                    raise ModuleError(addr, " ".join(tokens), reply.error_code or "UNKNOWN", reply.fields)
                return reply
            raise ModuleNotResponding(addr, " ".join(tokens), last_error)

    # -- internals ---------------------------------------------------------

    def _send(self, frame: bytes) -> None:
        log.debug("TX %s", frame.rstrip(b"\n").decode())
        if self._de is not None:
            self._de.transmit()
        started = time.monotonic()
        self._port.write(frame)
        self._port.flush()
        if self._de is not None:
            # flush() may return before the last stop bit has left the UART;
            # releasing DE early would cut the frame short.
            tx_time = len(frame) * 10 / self._baudrate
            remaining = tx_time - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
            self._de.receive()

    def _await_reply(self, addr: str, seq: int) -> Reply:
        deadline = time.monotonic() + self._reply_timeout
        bad_frame: FrameError | None = None
        while time.monotonic() < deadline:
            line = self._read_line(deadline)
            if line is None:
                break
            if not line.strip(b"\x00\r\n \xff"):
                continue
            try:
                frame = decode_frame(line)
            except FrameError as exc:
                log.debug("RX garbage %r (%s)", line, exc)
                bad_frame = exc
                continue
            if frame.start == REQUEST_START:
                continue  # our own echo (auto-direction adapters can echo)
            if frame.start == REPLY_START and frame.addr == addr and frame.seq == seq:
                log.debug("RX %s", line.rstrip(b"\n").decode(errors="replace"))
                self._last_rx = time.monotonic()
                return Reply.from_frame(frame)
            log.debug("RX stale/foreign frame %r", line)
        if bad_frame is not None:
            raise bad_frame
        raise TimeoutError(f"no reply from {addr} within {self._reply_timeout:.2f}s")

    def _read_line(self, deadline: float) -> bytes | None:
        while True:
            newline = self._rx.find(b"\n")
            if newline >= 0:
                line = bytes(self._rx[: newline + 1])
                del self._rx[: newline + 1]
                return line
            if time.monotonic() >= deadline:
                return None
            chunk = self._port.read(max(1, self._port.in_waiting))
            if chunk:
                self._rx.extend(chunk)
                if len(self._rx) > 4096:  # runaway noise, keep the tail only
                    del self._rx[:-256]
