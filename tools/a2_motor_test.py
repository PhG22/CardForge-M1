#!/usr/bin/env python3
"""Bench tool for the base stepper (module A2), over a USB-RS485 adapter.

Talks to `firmware/a2_motor_test/a2_motor_test.ino` (NOT a2_base.ino) with the frames
of docs/PROTOCOL.md, so it needs no rewiring: upload the test sketch to the bottom
Arduino, stop the API, plug the adapter into the same bus.

Standalone on purpose - only pyserial - so it can be copied to any laptop:

    python3 tools/a2_motor_test.py -p /dev/ttyUSB0 diag

Commands:

    ports                       list the serial ports it can see
    ping / status               liveness, pin levels, IR, step counter
    dir 0|1                     set the DIR pin and read it back
    period <us> / ramp 0|1      step period at full speed (625..20000), ramp on/off
    sleep 0|1                   0 = driver asleep, MOTOR GOES LIMP (base turns freely)
    reset 0|1 / m2 0|1          driver RESET and microstep-select pin
    zero                        reset the step counter
    step <+-steps>              move; the sign picks DIR (+ = clockwise)
    raw <0|1> <steps>           move with the DIR level FORCED - the direction test
    ab <steps> [-n cycles]      <steps> one way, <steps> back, N times: ends where
                                it started IF the direction really inverts
    seek <dir 0|1> <seen 0|1> [--limit N]
                                step until the homing IR reads <seen>
    dirtest                     automated: is the driver honouring DIR? (uses the IR strip)
    turn                        measure steps per base turn (tells 1/16 from full step)
    diag                        ping + pins + dirtest + turn, with a verdict
    stop                        stop a move that is running
    shell                       type commands by hand (STATUS, RAW 1 400, ...)

^C during a move sends STOP: the sketch stops the motor as soon as a byte reaches it.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    sys.exit("pyserial is missing:  pip install pyserial")

STEPS_PER_BASE_TURN = 12800     # expected at 1/16 step (a2_base.ino)
STEP_LIMIT = 26000              # the sketch refuses more than this per command
SLOW_PERIOD_US = 2500           # 400 steps/s: starts from standstill, no ramp, exact counts
AWAY_STEPS = 1200               # steps used to leave the strip in dirtest


# --------------------------------------------------------------------------- frames
class FrameError(Exception):
    pass


class ModuleError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"module answered ERR {code}")


def checksum(body: str) -> int:
    value = 0
    for byte in body.encode("ascii"):
        value ^= byte
    return value


def encode(addr: str, seq: int, tokens: list[str]) -> bytes:
    body = " ".join([addr, str(seq), *tokens])
    return f">{body}*{checksum(body):02X}\n".encode("ascii")


def decode(line: bytes) -> tuple[str, int, bool, str | None, dict[str, str]]:
    """-> (addr, seq, ok, error_code, fields). Noise before '<' is ignored."""
    text = line.decode("ascii", errors="replace").rstrip("\r\n")
    start = text.rfind("<")
    if start < 0:
        raise FrameError(f"no reply start char in {text!r}")
    rest = text[start + 1:]
    if len(rest) < 4 or rest[-3] != "*":
        raise FrameError(f"missing checksum in {rest!r}")
    body, cs_hex = rest[:-3], rest[-2:]
    try:
        received = int(cs_hex, 16)
    except ValueError:
        raise FrameError(f"bad checksum digits in {rest!r}") from None
    if received != checksum(body):
        raise FrameError(f"checksum mismatch in {rest!r} (expected {checksum(body):02X})")
    parts = body.split(" ")
    if len(parts) < 3 or not parts[1].isdigit():
        raise FrameError(f"malformed frame {rest!r}")
    addr, seq, head, tail = parts[0], int(parts[1]), parts[2], parts[3:]
    if head == "ERR":
        return addr, seq, False, (tail[0] if tail else "UNKNOWN"), {}
    if head != "OK":
        raise FrameError(f"reply must start with OK or ERR: {rest!r}")
    fields = {}
    for token in tail:
        key, sep, value = token.partition("=")
        if sep:
            fields[key.upper()] = value
    return addr, seq, True, None, fields


# --------------------------------------------------------------------------- link
class Link:
    def __init__(self, port: str, baud: int, addr: str, rts: bool, rts_invert: bool,
                 verbose: bool) -> None:
        self.addr = addr
        self.verbose = verbose
        self.rts = rts
        self.rts_invert = rts_invert
        self.seq = 0
        self.ser = serial.Serial(port, baud, timeout=0.1, write_timeout=2.0)
        if rts:
            self.ser.rts = not rts_invert       # DE low: listen
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def close(self) -> None:
        self.ser.close()

    # -- one transaction
    def send(self, tokens: list[str], timeout: float, retries: int) -> dict[str, str]:
        self.seq = (self.seq + 1) % 256
        frame = encode(self.addr, self.seq, tokens)
        attempt = 0
        while True:
            self._write(frame)
            try:
                fields = self._await_reply(self.seq, timeout)
            except (FrameError, TimeoutError) as exc:
                attempt += 1
                if attempt > retries:
                    raise
                print(f"  ! {exc}; retrying ({attempt}/{retries})", file=sys.stderr)
                continue
            return fields

    def _write(self, frame: bytes) -> None:
        self.ser.reset_input_buffer()           # drop a late reply to an earlier request
        if self.verbose:
            print(f"  TX {frame.decode('ascii').strip()}")
        if self.rts:
            self.ser.rts = self.rts_invert      # DE high: transmit
        self.ser.write(frame)
        self.ser.flush()
        if self.rts:
            self.ser.rts = not self.rts_invert  # back to listening

    def _await_reply(self, seq: int, timeout: float) -> dict[str, str]:
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"no reply in {timeout:.1f} s")
            try:
                chunk = self.ser.read(1)
            except KeyboardInterrupt:
                self.panic_stop()
                raise
            if not chunk:
                continue
            if chunk == b"\n":
                line, buf = bytes(buf), bytearray()
                if not line.strip():
                    continue
                if self.verbose:
                    print(f"  RX {line.decode('ascii', 'replace').strip()}")
                addr, got_seq, ok, code, fields = decode(line)
                if addr != self.addr or got_seq != seq:
                    continue                    # someone else's frame, or a stale one
                if not ok:
                    raise ModuleError(code or "UNKNOWN")
                return fields
            buf += chunk

    def panic_stop(self) -> None:
        """Any byte on the bus stops a running move; STOP is the polite way to say it."""
        try:
            self._write(encode(self.addr, (self.seq + 1) % 256, ["STOP"]))
        except Exception:
            pass

    # -- helpers
    def ask(self, *tokens: str, timeout: float = 1.0, retries: int = 2) -> dict[str, str]:
        return self.send(list(tokens), timeout, retries)

    def status(self) -> dict[str, str]:
        return self.ask("STATUS")

    def move(self, *tokens: str, steps: int) -> dict[str, str]:
        """A move: timeout from the step count, and no retries (a retry aborts a move)."""
        st = self.status()
        period = int(st.get("PER", SLOW_PERIOD_US))
        ramped = st.get("RAMP") == "1" and period < 5000
        est = steps * period / 1e6
        timeout = min(est * 1.3 + (8.0 if ramped else 0.0) + 4.0, 900.0)
        return self.send(list(tokens), timeout, retries=0)

    def slow_mode(self) -> None:
        """Constant 400 steps/s: no ramp, so a move stops exactly where the IR fired."""
        self.ask("PER", str(SLOW_PERIOD_US))
        self.ask("RAMP", "0")


# --------------------------------------------------------------------------- printing
def show(fields: dict[str, str], prefix: str = "  ") -> None:
    print(prefix + " ".join(f"{k}={v}" for k, v in fields.items()))


def show_status(st: dict[str, str]) -> None:
    print(f"  DIR={st.get('DIR')} (pin reads {st.get('DIRRD')})   "
          f"SLEEP={st.get('SLP')}  RESET={st.get('RST')}  M2={st.get('M2')}")
    print(f"  homing IR ZERO={st.get('ZERO')}   step counter POS={st.get('POS')}   "
          f"period={st.get('PER')} us   ramp={st.get('RAMP')}")


# --------------------------------------------------------------------------- routines
def find_strip(link: Link, direction: int) -> dict[str, str] | None:
    """Turn until the homing IR sees the strip, ending on its edge. None = not found."""
    st = link.status()
    if st.get("ZERO") == "1":
        print("  already on the strip: stepping off it backwards first")
        link.move("SEEK", str(1 - direction), "0", "4000", steps=4000)
    limit = int(STEPS_PER_BASE_TURN * 1.3)
    res = link.move("SEEK", str(direction), "1", str(limit), steps=limit)
    show(res)
    return res if res.get("HIT") == "1" else None


def dirtest(link: Link) -> bool:
    """Does the driver obey DIR? Verdict from the IR strip, not from watching it."""
    print("\n== DIR pin ==")
    ok_pin = True
    for level in ("0", "1"):
        res = link.ask("DIR", level)
        read = res.get("DIRRD")
        good = read == level
        ok_pin = ok_pin and good
        print(f"  DIR={level} -> pin reads {read}  {'ok' if good else 'MISMATCH'}")
    if not ok_pin:
        print("  !! D6 does not follow what the firmware writes: the pin is shorted to")
        print("     GND/5 V, or something else is driving it. Check that wire first.")

    st = link.status()
    if st.get("SLP") != "1" or st.get("RST") != "1":
        print(f"  !! driver disabled (SLEEP={st.get('SLP')} RESET={st.get('RST')}); "
              "run:  sleep 1  and  reset 1")
        return False

    print("\n== finding the strip (reference for the measurements) ==")
    link.slow_mode()
    if find_strip(link, 1) is None:
        print("  strip not found turning with DIR=1; trying DIR=0")
        if find_strip(link, 0) is None:
            print("\n  VERDICT: the homing IR never saw the strip in either direction.")
            print("  Either the motor is not turning at all, or the IR (A5) is not reading.")
            print("  Check `status` while you pass the strip in front of the sensor by hand:")
            print("  ZERO must go 1. If the motor is silent, test:  sleep 1 / reset 1 / m2 1")
            print("  and  raw 1 400  (listen for the motor).")
            return False

    print("\n== test A: step away with DIR=0, come back with DIR=1 ==")
    away = link.move("RAW", "0", str(AWAY_STEPS), steps=AWAY_STEPS)
    show(away)
    left_a = away.get("ZERO") == "0"
    if not left_a:
        print("  !! still on the strip after 1200 steps: the motor did not move,")
        print("     or the IR is stuck at 1.")
    back_a = link.move("SEEK", "1", "1", str(2 * AWAY_STEPS), steps=2 * AWAY_STEPS)
    show(back_a)
    hit_a = back_a.get("HIT") == "1"

    print("\n== test B: step away with DIR=1, come back with DIR=0 ==")
    away_b = link.move("RAW", "1", str(AWAY_STEPS), steps=AWAY_STEPS)
    show(away_b)
    back_b = link.move("SEEK", "0", "1", str(2 * AWAY_STEPS), steps=2 * AWAY_STEPS)
    show(back_b)
    hit_b = back_b.get("HIT") == "1"

    around = None
    if not hit_a and not hit_b:
        print("\n== test C: did it go all the way round instead of coming back? ==")
        # If the driver ignores DIR, both moves above went the SAME physical way, so the
        # strip is one full turn ahead - not 1200 steps behind.
        limit = int(STEPS_PER_BASE_TURN * 1.2)
        around = link.move("SEEK", "1", "1", str(limit), steps=limit)
        show(around)

    print("\n== VERDICT ==")
    if hit_a and hit_b:
        print(f"  DIR works both ways: the strip came back after {back_a.get('N')} steps")
        print(f"  with DIR=1 and after {back_b.get('N')} steps with DIR=0 (sent {AWAY_STEPS}).")
        print("  The driver IS inverting the rotation. If the machine still turns the wrong")
        print("  way, the problem is the CW_DIR / HOMING_DIR convention in a2_base.ino,")
        print("  not the wiring. Second opinion:  seek 1 1   then  ab 1600 -n 3")
        print("  (it must still be on the strip, ZERO=1, at the end).")
        return True
    if hit_a != hit_b:
        stuck = "0" if hit_a else "1"
        moving = "1" if hit_a else "0"
        print(f"  DIR is NOT being inverted. Stepping with DIR={moving} moved the base,")
        print(f"  but with DIR={stuck} it did not come back - it kept going the same way.")
        print("  The driver is not seeing the DIR level change. Check, in this order:")
        print("   1. the D6 -> driver DIR wire (continuity, and that it is on DIR and not")
        print("      on the pin next to it);")
        print("   2. the driver's logic ground: GND of the module tied to the Arduino GND;")
        print("   3. swap the driver module - a blown DIR input is common after a")
        print("      hot-unplug of the motor with the power on.")
        return False
    if around is not None and around.get("HIT") == "1" and int(around["N"]) > 4 * AWAY_STEPS:
        print(f"  DIR is NOT being inverted at all. Neither DIR level brought the base")
        print(f"  back to the strip, and it then found the strip {around['N']} steps further")
        print("  on: every move went the same physical way, whatever DIR said.")
        print("  The driver is not seeing the DIR level change. Check, in this order:")
        print("   1. the D6 -> driver DIR wire (continuity, and that it sits on DIR and")
        print("      not on the pin next to it);")
        print("   2. the driver's logic ground: module GND tied to the Arduino GND, and")
        print("      the module's VDD/logic supply present;")
        print("   3. swap the driver module - a dead DIR input is common after unplugging")
        print("      the motor with the power on.")
        return False
    if not left_a:
        print("  The base never left the strip: the motor is not turning (or the IR reads 1")
        print("  all the time). Check SLEEP/RESET/M2, the motor coils and the motor supply:")
        print("    sleep 1 / reset 1 / m2 1  then  raw 1 400  (you should hear it step)")
        return False
    print("  The base moved off the strip but never came back in either direction, and it")
    print("  did not go a full turn either: the motor is losing steps / stalling, or the")
    print("  strip is being passed too fast. Repeat with:  period 5000  and then  dirtest")
    return False


def measure_turn(link: Link) -> None:
    """Steps per base turn, measured with the strip. Tells 1/16 from full step.

    Measured edge to edge: leave the strip, go round until it shows up again, then
    cross it. That sum is one exact turn wherever the base happened to start.
    """
    print("\n== measuring one base turn (takes ~40 s at 400 steps/s) ==")
    link.slow_mode()
    if link.status().get("ZERO") != "1" and find_strip(link, 1) is None:
        print("  strip not found: cannot measure.")
        return
    out = link.move("SEEK", "1", "0", "4000", steps=4000)
    show(out)
    if out.get("HIT") != "1":
        print("  the IR never cleared: strip wider than 4000 steps? aborting.")
        return
    limit = int(STEPS_PER_BASE_TURN * 1.4)
    around = link.move("SEEK", "1", "1", str(limit), steps=limit)
    show(around)
    if around.get("HIT") != "1":
        print(f"  strip not seen again within {limit} steps: the base lost steps, or it")
        print("  is turning much less than the firmware thinks (check the gearing).")
        return
    width = link.move("SEEK", "1", "0", "4000", steps=4000)
    show(width)
    if width.get("HIT") != "1":
        print("  the IR never cleared on the second pass: aborting.")
        return
    turn = int(around["N"]) + int(width["N"])
    ratio = turn / STEPS_PER_BASE_TURN
    print(f"\n  one base turn = {turn} steps "
          f"({around['N']} between the strip edges + {width['N']} across the strip), "
          f"firmware assumes {STEPS_PER_BASE_TURN}")
    if 0.95 <= ratio <= 1.05:
        print("  matches: the driver is in 1/16 step, as a2_base.ino assumes.")
    elif 0.05 <= ratio <= 0.08:
        print("  about 1/16 of it: THE DRIVER IS IN FULL STEP, not 1/16.")
        print("  That is what an A4988 does with only MS3 (D2) high - on an A4988 you need")
        print("  MS1+MS2+MS3 all high for 1/16. A DRV8825 only needs M2 high. So either the")
        print("  module is an A4988 (wire MS1 and MS2 high too), or D2 is not reaching M2.")
    else:
        print(f"  off by {ratio:.2f}x: wrong microstep setting or wrong gearing constant.")


# --------------------------------------------------------------------------- commands
def cmd_ports(_args) -> int:
    found = list(list_ports.comports())
    if not found:
        print("no serial ports found (is the USB-RS485 adapter plugged in?)")
    for port in found:
        print(f"{port.device:20} {port.description}")
    return 0


def run(link: Link, args) -> int:
    name = args.command
    if name == "ping":
        show(link.ask("PING"))
    elif name == "status":
        show_status(link.status())
    elif name == "dir":
        show(link.ask("DIR", str(args.level)))
    elif name == "period":
        show(link.ask("PER", str(args.us)))
    elif name == "ramp":
        show(link.ask("RAMP", str(args.on)))
    elif name == "sleep":
        show(link.ask("SLP", str(args.on)))
    elif name == "reset":
        show(link.ask("RST", str(args.on)))
    elif name == "m2":
        show(link.ask("M2", str(args.on)))
    elif name == "zero":
        show(link.ask("ZERO"))
    elif name == "stop":
        show(link.ask("STOP"))
    elif name == "step":
        show(link.move("STEP", str(args.steps), steps=abs(args.steps)))
    elif name == "raw":
        show(link.move("RAW", str(args.level), str(args.steps), steps=args.steps))
    elif name == "ab":
        # POS only counts what was commanded, so it comes back to 0 even when the driver
        # ignores DIR. The honest check is the homing IR: start on the strip and the base
        # must be back on it at the end.
        on_strip = link.status().get("ZERO") == "1"
        link.ask("ZERO")
        res = link.move("AB", str(args.steps), str(args.cycles),
                        steps=args.steps * 2 * args.cycles)
        show(res)
        print(f"\n  {res.get('CYCLES')} cycles of +-{args.steps} steps done "
              f"(POS={res.get('POS')} is only what was commanded).")
        if not on_strip:
            print("  Watch the base: it must end where it started. For a measured answer,")
            print("  park it on the strip (`seek 1 1`) and run this again, or run `dirtest`.")
        elif res.get("ZERO") == "1":
            print("  It started on the strip and ended on it: the direction really inverts.")
        else:
            print("  It started on the strip and is NOT on it any more: the base walked away")
            print("  in one direction, so the driver is ignoring DIR. Run `dirtest`.")
    elif name == "seek":
        show(link.move("SEEK", str(args.dir), str(args.seen), str(args.limit),
                       steps=args.limit))
    elif name == "dirtest":
        return 0 if dirtest(link) else 1
    elif name == "turn":
        measure_turn(link)
    elif name == "diag":
        print("== PING ==")
        show(link.ask("PING"))
        print("\n== STATUS ==")
        st = link.status()
        show_status(st)
        if st.get("SLP") != "1" or st.get("RST") != "1":
            print("\n  the driver is disabled, nothing can move. Enable it and run diag again:")
            print("    sleep 1    reset 1")
            return 1
        good = dirtest(link)
        measure_turn(link)
        print("\n(settings left at 400 steps/s with no ramp; power-cycle the Arduino, or")
        print(" run `period 625` and `ramp 1`, to get the production profile back)")
        return 0 if good else 1
    elif name == "shell":
        print("type a command (STATUS, DIR 1, RAW 1 400, SEEK 1 1 4000, STOP), "
              "empty line or ^D to quit")
        while True:
            try:
                raw = input("A2> ").strip()
            except EOFError:
                break
            if not raw:
                break
            tokens = raw.upper().split()
            try:
                steps = max((abs(int(t)) for t in tokens[1:] if t.lstrip("-+").isdigit()),
                            default=0)
                show(link.move(*tokens, steps=min(steps, STEP_LIMIT)))
            except (ModuleError, FrameError, TimeoutError) as exc:
                print(f"  {exc}")
    else:  # pragma: no cover
        raise SystemExit(f"unknown command {name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--port", default="/dev/ttyUSB0", help="serial port of the adapter")
    parser.add_argument("-b", "--baud", type=int, default=9600)
    parser.add_argument("--addr", default="A2", help="module address (default A2)")
    parser.add_argument("--rts", action="store_true",
                        help="adapter without auto-direction: drive DE/RE from RTS")
    parser.add_argument("--rts-invert", action="store_true", help="flip the RTS polarity")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every frame")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, helptext in (("ports", "list serial ports"), ("ping", "liveness check"),
                           ("status", "pin levels, IR, step counter"),
                           ("zero", "reset the step counter"),
                           ("stop", "stop a running move"),
                           ("dirtest", "automated direction check (uses the IR strip)"),
                           ("turn", "measure steps per base turn"),
                           ("diag", "ping + pins + dirtest + turn"),
                           ("shell", "type commands by hand")):
        sub.add_parser(name, help=helptext)

    p = sub.add_parser("dir", help="set the DIR pin and read it back")
    p.add_argument("level", type=int, choices=(0, 1))
    p = sub.add_parser("period", help="step period at full speed, us (625..20000)")
    p.add_argument("us", type=int)
    for name, helptext in (("ramp", "ramp up from 200 steps/s (0/1)"),
                           ("sleep", "driver SLEEP pin: 0 = motor limp"),
                           ("reset", "driver RESET pin: 0 = driver in reset"),
                           ("m2", "microstep-select pin D2")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("on", type=int, choices=(0, 1))
    p = sub.add_parser("step", help="move; the sign picks the direction")
    p.add_argument("steps", type=int)
    p = sub.add_parser("raw", help="move with the DIR level forced")
    p.add_argument("level", type=int, choices=(0, 1))
    p.add_argument("steps", type=int)
    p = sub.add_parser("ab", help="back and forth: ends where it started if DIR works")
    p.add_argument("steps", type=int)
    p.add_argument("-n", "--cycles", type=int, default=3)
    p = sub.add_parser("seek", help="step until the homing IR reads <seen>")
    p.add_argument("dir", type=int, choices=(0, 1))
    p.add_argument("seen", type=int, choices=(0, 1))
    p.add_argument("--limit", type=int, default=STEPS_PER_BASE_TURN)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "ports":
        return cmd_ports(args)
    try:
        link = Link(args.port, args.baud, args.addr, args.rts, args.rts_invert, args.verbose)
    except serial.SerialException as exc:
        print(f"cannot open {args.port}: {exc}", file=sys.stderr)
        print("run `ports` to list what is plugged in", file=sys.stderr)
        return 2
    try:
        return run(link, args)
    except KeyboardInterrupt:
        print("\ninterrupted: STOP sent", file=sys.stderr)
        return 130
    except TimeoutError as exc:
        print(f"\n{exc}", file=sys.stderr)
        print("Is a2_motor_test.ino uploaded (not a2_base.ino)? Is the API stopped?\n"
              "Are A/B swapped on the adapter? Does the adapter need --rts?", file=sys.stderr)
        return 1
    except (FrameError, ModuleError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
