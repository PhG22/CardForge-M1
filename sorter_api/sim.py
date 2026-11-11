"""Software stand-ins for the two Arduinos, so the API can run without hardware.

SimSerialPort looks like a pyserial port to the bus: frames go through the real
encoder/decoder and checksum code. The fake A1/A2 follow docs/PROTOCOL.md, so
this file doubles as a reference for the firmware behaviour (non-blocking
actions, STATE/ERR reporting, duplicate-request handling).

Mechanics are simplified: a card takes ~0.7 s to feed, homing ~2 s, each tray
step ~0.4 s, and a card on the platform falls ~0.15 s after the flap opens,
landing in whichever tray is under the platform.
"""

from __future__ import annotations

import logging
import threading
import time

from .modules import TRAY_COUNT
from .protocol import REPLY_START, REQUEST_START, Frame, FrameError, decode_frame, encode_frame

log = logging.getLogger(__name__)

FLAP_OPEN_BELOW = 40        # flap angles below this let the card fall
DUPLICATE_WINDOW_S = 1.0    # see PROTOCOL.md "Retries and duplicates"
READ_ONLY = {"PING", "STATUS"}


class SimError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _int_arg(args: tuple[str, ...], index: int, lo: int, hi: int) -> int:
    try:
        value = int(args[index])
    except (IndexError, ValueError):
        raise SimError("BAD_ARG")
    if not lo <= value <= hi:
        raise SimError("BAD_ARG")
    return value


class _SimDevice:
    address = "??"

    def __init__(self, world: "SimWorld") -> None:
        self.world = world
        self.state = "IDLE"
        self.error: str | None = None
        self.executed: list[str] = []           # commands actually executed (for tests)
        self._last: tuple[int, str, bytes, float] | None = None

    def handle(self, frame: Frame, now: float) -> bytes:
        command = frame.tokens[0]
        if (self._last and command not in READ_ONLY and frame.seq == self._last[0]
                and frame.body == self._last[1] and now - self._last[3] < DUPLICATE_WINDOW_S):
            return self._last[2]                # retry of a request we already executed
        try:
            fields = self.execute(command, frame.tokens[1:], now)
            tokens = ["OK", *fields]
        except SimError as exc:
            tokens = ["ERR", exc.code]
        self.executed.append(" ".join(frame.tokens))
        reply = encode_frame(REPLY_START, self.address, frame.seq, tokens)
        self._last = (frame.seq, frame.body, reply, now)
        return reply

    def _error_field(self) -> list[str]:
        return [f"ERR={self.error}"] if self.state == "ERROR" and self.error else []

    def _begin_action(self) -> None:
        if self.state == "BUSY":
            raise SimError("BUSY")
        self.state = "BUSY"
        self.error = None

    def _fail(self, code: str) -> None:
        self.state = "ERROR"
        self.error = code

    def execute(self, command: str, args: tuple[str, ...], now: float) -> list[str]:
        raise NotImplementedError

    def tick(self, now: float) -> None:
        pass


class SimTop(_SimDevice):
    """A1: feed roller, flap, stack IR, platform IR."""

    address = "A1"

    def __init__(self, world: "SimWorld") -> None:
        super().__init__(world)
        self.stack = 0
        self.platform = False
        self.flap = 90
        self.roller = False
        self.jammed = False                 # fault injection: cards never arrive
        self.dropped = 0                    # cards that fell from the platform
        self._feed_done_at: float | None = None
        self._feed_fail_at: float | None = None
        self._drop_at: float | None = None
        self.light: int | None = None       # optional LIGHT command

    def tick(self, now: float) -> None:
        if self._feed_fail_at is not None:
            if not self.jammed and self.stack > 0 and now >= self._feed_done_at:
                self.stack -= 1
                self.platform = True
                self._end_feed()
                self.state = "IDLE"
            elif now >= self._feed_fail_at:
                self._end_feed()
                self._fail("FEED_TIMEOUT")
        if self._drop_at is not None and now >= self._drop_at:
            self._drop_at = None
            self.platform = False
            self.dropped += 1
            if self.world.bottom_present:
                self.world.bottom.receive_card()

    def _end_feed(self) -> None:
        self.roller = False
        self._feed_done_at = self._feed_fail_at = None

    def execute(self, command: str, args: tuple[str, ...], now: float) -> list[str]:
        if command == "PING":
            return ["FW=sim-1.0"]
        if command == "STATUS":
            return [
                f"STATE={self.state}",
                f"STACK={int(self.stack > 0)}",
                f"PLAT={int(self.platform)}",
                f"FLAP={self.flap}",
                f"ROLLER={int(self.roller)}",
                *self._error_field(),
            ]
        if command == "ROLLER":
            if args != ("STOP",):
                raise SimError("BAD_ARG")
            if self._feed_fail_at is not None:
                self._end_feed()
                self.state = "IDLE"
            self.roller = False
            return []
        if command == "FLAP":
            angle = _int_arg(args, 0, 0, 180)
            self.flap = angle
            if angle < FLAP_OPEN_BELOW and self.platform and self._drop_at is None:
                self._drop_at = now + 0.15 * self.world.scale
            elif angle >= FLAP_OPEN_BELOW:
                self._drop_at = None
            return []
        if command == "FEED":
            timeout_ms = _int_arg(args, 0, 100, 60000)
            self._begin_action()
            if self.platform:                   # already a card there: nothing to do
                self.state = "IDLE"
                return []
            self.roller = True
            self._feed_done_at = now + 0.7 * self.world.scale
            self._feed_fail_at = now + timeout_ms / 1000
            return []
        if command == "STOP":
            self._end_feed()
            self.state, self.error = "IDLE", None
            return []
        if command == "LIGHT" and self.world.optional_commands:
            self.light = _int_arg(args, 0, 0, 100)
            return []
        raise SimError("BAD_CMD")


# Same geometry as firmware/a2_base: packed ring, home (strip) at tray 3
TRAY_STEPS = (3306, 1653, 0, -1653, -3306, -4960)
HOME_TRAY = 3


class SimBottom(_SimDevice):
    """A2: stepper base, zero IR, 6 tray IRs."""

    address = "A2"

    def __init__(self, world: "SimWorld") -> None:
        super().__init__(world)
        self.homed = False
        self.pos = 0
        self.steps = 0
        self._target_steps = 0
        self.trays = [False] * TRAY_COUNT
        self.cards_lost = 0
        self._target: int | None = None
        self._done_at: float | None = None

    def tick(self, now: float) -> None:
        if self._done_at is not None and now >= self._done_at:
            homing = self._target is None
            if homing:
                self.homed, self.pos, self.steps = True, HOME_TRAY, 0
            else:
                self.pos = self._target
                self.steps = self._target_steps
            self._done_at = self._target = None
            self.state = "IDLE"

    def receive_card(self) -> None:
        if self.state == "IDLE" and 1 <= self.pos <= TRAY_COUNT:
            self.trays[self.pos - 1] = True
        else:
            self.cards_lost += 1
            log.warning("sim: card fell while the base was not at a tray")

    def execute(self, command: str, args: tuple[str, ...], now: float) -> list[str]:
        if command == "PING":
            return ["FW=sim-1.0"]
        if command == "STATUS":
            return [
                f"STATE={self.state}",
                f"HOMED={int(self.homed)}",
                f"POS={self.pos}",
                f"ZERO={int(self.homed and self.steps == 0 and self.state != 'BUSY')}",
                "TRAYS=" + "".join("1" if t else "0" for t in self.trays),
                f"STEPS={self.steps}",
                "DRIFT=0",
                *self._error_field(),
            ]
        if command == "HOME":
            _int_arg(args, 0, 100, 60000)
            self._begin_action()
            self.homed, self.pos, self._target = False, 0, None
            self._done_at = now + 2.0 * self.world.scale
            return []
        if command in ("GOTO", "MOVE"):
            if command == "GOTO":
                tray = _int_arg(args, 0, 1, TRAY_COUNT)
                target = TRAY_STEPS[tray - 1]
            else:
                target = _int_arg(args, 0, -20000, 20000)
                tray = TRAY_STEPS.index(target) + 1 if target in TRAY_STEPS else 0
            if not self.homed:
                raise SimError("NOT_HOMED")
            self._begin_action()
            self._target, self._target_steps = tray, target
            duration = (0.2 + abs(target - self.steps) / 1400) * self.world.scale
            self._done_at = now + duration
            self.pos = 0
            return [f"T={int(duration * 1000)}"]
        if command == "STOP":
            if self._done_at is not None:
                if self._target is None:
                    self.homed = False      # homing interrupted
                self.pos = 0
            self._done_at = self._target = None
            self.state, self.error = "IDLE", None
            return []
        raise SimError("BAD_CMD")


class SimWorld:
    def __init__(self, stack_cards: int = 8, time_scale: float = 1.0, bottom_present: bool = True,
                 optional_commands: bool = True) -> None:
        self.lock = threading.Lock()
        self.optional_commands = optional_commands  # False = firmware without LIGHT (a1_top.ino today)
        self.scale = time_scale
        self.bottom_present = bottom_present  # False = top-only bench, nothing answers as A2
        self.top = SimTop(self)
        self.bottom = SimBottom(self)
        self.top.stack = stack_cards
        self.offline: set[str] = set() if bottom_present else {"A2"}   # these addresses never answer
        self.drop_replies = 0               # fault injection: lose the next N replies

    def handle_line(self, line: bytes) -> bytes | None:
        try:
            frame = decode_frame(line, expect_start=REQUEST_START)
        except FrameError:
            return None                      # devices silently ignore bad frames
        with self.lock:
            now = time.monotonic()
            self.top.tick(now)
            self.bottom.tick(now)
            device = {"A1": self.top, "A2": self.bottom}.get(frame.addr)
            if device is None or frame.addr in self.offline:
                return None
            reply = device.handle(frame, now)
            if self.drop_replies > 0:
                self.drop_replies -= 1
                return None
            return reply

    def set_state(self, *, stack_cards=None, trays_with_cards=None, platform_has_card=None,
                  top_jammed=None, offline_modules=None) -> None:
        with self.lock:
            if stack_cards is not None:
                self.top.stack = stack_cards
            if trays_with_cards is not None:
                self.bottom.trays = [i + 1 in trays_with_cards for i in range(TRAY_COUNT)]
            if platform_has_card is not None:
                self.top.platform = platform_has_card
            if top_jammed is not None:
                self.top.jammed = top_jammed
            if offline_modules is not None:
                self.offline = set(offline_modules)

    def describe(self) -> dict:
        with self.lock:
            now = time.monotonic()
            self.top.tick(now)
            self.bottom.tick(now)
            return {
                "stack_cards": self.top.stack,
                "platform_has_card": self.top.platform,
                "flap_angle": self.top.flap,
                "roller_running": self.top.roller,
                "cards_dropped": self.top.dropped,
                "top_jammed": self.top.jammed,
                "base_position": self.bottom.pos,
                "base_steps": self.bottom.steps,
                "base_homed": self.bottom.homed,
                "trays_with_cards": [i + 1 for i, t in enumerate(self.bottom.trays) if t],
                "cards_lost": self.bottom.cards_lost,
                "offline_modules": sorted(self.offline),
                "light_level": self.top.light,
            }


class SimSerialPort:
    """Quacks like serial.Serial for RS485Bus."""

    def __init__(self, world: SimWorld, timeout: float = 0.02) -> None:
        self.world = world
        self.timeout = timeout
        self._tx = bytearray()
        self._rx = bytearray()
        self._cond = threading.Condition()

    def write(self, data: bytes) -> int:
        self._tx.extend(data)
        while (newline := self._tx.find(b"\n")) >= 0:
            line = bytes(self._tx[: newline + 1])
            del self._tx[: newline + 1]
            reply = self.world.handle_line(line)
            if reply:
                with self._cond:
                    self._rx.extend(reply)
                    self._cond.notify_all()
        return len(data)

    def flush(self) -> None:
        pass

    @property
    def in_waiting(self) -> int:
        return len(self._rx)

    def read(self, size: int = 1) -> bytes:
        with self._cond:
            if not self._rx:
                self._cond.wait(self.timeout)
            data = bytes(self._rx[:size])
            del self._rx[:size]
            return data

    def reset_input_buffer(self) -> None:
        with self._cond:
            self._rx.clear()

    def close(self) -> None:
        pass
