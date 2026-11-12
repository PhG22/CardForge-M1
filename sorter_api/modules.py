"""Drivers for the two Arduino modules. Each method is one RS-485 command.

Long actions (FEED, HOME, GOTO) only *start* on the Arduino: the reply comes back
immediately and the Pi then polls STATUS until the module is IDLE again. That
keeps the bus free while motors move, so the Pi can talk to the other module or
send STOP at any time.
"""

from __future__ import annotations

from dataclasses import dataclass

from .bus import RS485Bus
from .protocol import FrameError, Reply

TRAY_COUNT = 6
HOME_TRAY = 3          # the tray over the homing strip (a2_base.ino); STEPS=0 there
MOVE_LIMIT = 20000          # a2_base accepts MOVE -20000 .. 20000


class ModuleState:
    IDLE = "IDLE"
    BUSY = "BUSY"
    ERROR = "ERROR"


def _flag(reply: Reply, key: str) -> bool:
    value = _field(reply, key)
    if value not in ("0", "1"):
        raise FrameError(f"{reply.addr}: {key} must be 0 or 1, got {value!r}")
    return value == "1"


def _int(reply: Reply, key: str) -> int:
    value = _field(reply, key)
    try:
        return int(value)
    except ValueError as exc:
        raise FrameError(f"{reply.addr}: {key} must be an integer, got {value!r}") from exc


def _field(reply: Reply, key: str) -> str:
    try:
        return reply.fields[key]
    except KeyError as exc:
        raise FrameError(f"{reply.addr}: STATUS reply is missing {key}") from exc


def _duration(reply: Reply) -> int | None:
    value = reply.fields.get("T")
    return int(value) if value is not None and value.isdigit() else None


def _state(reply: Reply) -> str:
    state = _field(reply, "STATE")
    if state not in (ModuleState.IDLE, ModuleState.BUSY, ModuleState.ERROR):
        raise FrameError(f"{reply.addr}: unknown STATE {state!r}")
    return state


@dataclass(frozen=True)
class TopStatus:
    state: str
    stack_has_card: bool
    platform_has_card: bool
    flap_angle: int
    roller_running: bool
    error: str | None
    feed_attempt: int | None = None     # RF05: pull attempt of the current / last FEED (firmware 1.1+)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "stack_has_card": self.stack_has_card,
            "platform_has_card": self.platform_has_card,
            "flap_angle": self.flap_angle,
            "roller_running": self.roller_running,
            "error": self.error,
            "feed_attempt": self.feed_attempt,
        }


@dataclass(frozen=True)
class BottomStatus:
    state: str
    homed: bool
    position: int                   # 1..6, 0 = not at a tray
    zero_sensor: bool
    trays_with_cards: tuple[int, ...]
    error: str | None
    steps: int | None = None        # absolute position in steps from home (firmware a2_base)
    drift: int | None = None        # position error found by the last re-HOME (0 = no lost steps)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "homed": self.homed,
            "position": self.position,
            "zero_sensor": self.zero_sensor,
            "trays_with_cards": list(self.trays_with_cards),
            "steps": self.steps,
            "drift": self.drift,
            "error": self.error,
        }


class TopModule:
    """A1: feed roller (360 servo), stopper flap (180 servo), stack + platform IR."""

    ADDRESS = "A1"

    def __init__(self, bus: RS485Bus) -> None:
        self._bus = bus

    def ping(self) -> Reply:
        return self._bus.transact(self.ADDRESS, "PING")

    def status(self) -> TopStatus:
        r = self._bus.transact(self.ADDRESS, "STATUS")
        return TopStatus(
            state=_state(r),
            stack_has_card=_flag(r, "STACK"),
            platform_has_card=_flag(r, "PLAT"),
            flap_angle=_int(r, "FLAP"),
            roller_running=_flag(r, "ROLLER"),
            error=r.fields.get("ERR"),
            feed_attempt=_int(r, "ATTEMPT") if "ATTEMPT" in r.fields else None,
        )

    def roller_stop(self) -> None:
        self._bus.transact(self.ADDRESS, "ROLLER", "STOP")

    def set_flap(self, angle: int) -> None:
        if not 0 <= angle <= 180:
            raise ValueError(f"flap angle out of range: {angle}")
        self._bus.transact(self.ADDRESS, "FLAP", int(angle))

    def start_feed(self, timeout_ms: int) -> None:
        """Run the roller until the platform IR sees a card (or timeout_ms passes)."""
        self._bus.transact(self.ADDRESS, "FEED", int(timeout_ms))

    def stop(self) -> None:
        self._bus.transact(self.ADDRESS, "STOP")

    # Optional command (docs/PROTOCOL.md 5.1). Firmware without it answers ERR BAD_CMD.
    def set_light(self, level: int) -> None:
        """Chamber LED strips, 0-100 % (RNF08 / RNF09)."""
        if not 0 <= level <= 100:
            raise ValueError(f"light level out of range: {level}")
        self._bus.transact(self.ADDRESS, "LIGHT", int(level))


class BottomModule:
    """A2: rotating base (stepper), zero-position IR, 6 tray IR sensors."""

    ADDRESS = "A2"

    def __init__(self, bus: RS485Bus) -> None:
        self._bus = bus

    def ping(self) -> Reply:
        return self._bus.transact(self.ADDRESS, "PING")

    def status(self) -> BottomStatus:
        r = self._bus.transact(self.ADDRESS, "STATUS")
        trays = _field(r, "TRAYS")
        if len(trays) != TRAY_COUNT or set(trays) - {"0", "1"}:
            raise FrameError(f"A2: TRAYS must be {TRAY_COUNT} digits of 0/1, got {trays!r}")
        return BottomStatus(
            state=_state(r),
            homed=_flag(r, "HOMED"),
            position=_int(r, "POS"),
            zero_sensor=_flag(r, "ZERO"),
            trays_with_cards=tuple(i + 1 for i, c in enumerate(trays) if c == "1"),
            error=r.fields.get("ERR"),
            steps=_int(r, "STEPS") if "STEPS" in r.fields else None,
            drift=_int(r, "DRIFT") if "DRIFT" in r.fields else None,
        )

    def start_home(self, timeout_ms: int, hint_steps: int | None = None) -> None:
        """Find the reflective strip. The base then sits at the home tray (tray 3).
        hint_steps: where the base was last seen (steps from home). With it, a2_base 1.1 goes
        fast to just before the strip instead of searching (RF08); older firmware ignores it."""
        if hint_steps is None:
            self._bus.transact(self.ADDRESS, "HOME", int(timeout_ms))
        else:
            self._bus.transact(self.ADDRESS, "HOME", int(timeout_ms), int(hint_steps))

    def start_goto(self, tray: int) -> int | None:
        """Returns how long the move will take (ms), if the firmware says (OK T=<ms>)."""
        if not 1 <= tray <= TRAY_COUNT:
            raise ValueError(f"tray out of range: {tray}")
        return _duration(self._bus.transact(self.ADDRESS, "GOTO", int(tray)))

    def start_move(self, steps: int) -> int | None:
        """Turn to an absolute position, in steps from home (+ = clockwise)."""
        if not -MOVE_LIMIT <= steps <= MOVE_LIMIT:
            raise ValueError(f"steps out of range: {steps}")
        return _duration(self._bus.transact(self.ADDRESS, "MOVE", int(steps)))

    def stop(self) -> None:
        self._bus.transact(self.ADDRESS, "STOP")
