"""RS-485 frame format shared by the Raspberry Pi (master) and the Arduinos.

Full spec: docs/PROTOCOL.md. In short, every frame is one ASCII line:

    request  (Pi -> Arduino):   >A1 17 FEED 4000*50\n
    reply    (Arduino -> Pi):   <A1 17 OK*72\n
                                <A1 17 ERR BUSY*0E\n
                                <A2 18 OK STATE=IDLE HOMED=1 POS=1 ZERO=1 TRAYS=000000*49\n

    start char  '>' for requests, '<' for replies
    address     A1 (top module) or A2 (bottom module)
    seq         0-255, chosen by the Pi, echoed back in the reply
    tokens      command + arguments (request) or OK/ERR + fields (reply)
    checksum    XOR of every byte between the start char and '*', 2 uppercase hex digits
"""

from __future__ import annotations

from dataclasses import dataclass, field

REQUEST_START = ">"
REPLY_START = "<"
MAX_FRAME_LEN = 96          # bytes including '\n'; keep Arduino buffers small
_FORBIDDEN = set("<>*\n\r ")


class FrameError(ValueError):
    """A line that is not a valid frame (bad checksum, malformed, too long)."""


def checksum(body: str) -> int:
    value = 0
    for byte in body.encode("ascii"):
        value ^= byte
    return value


@dataclass(frozen=True)
class Frame:
    start: str              # REQUEST_START or REPLY_START
    addr: str
    seq: int
    tokens: tuple[str, ...]

    @property
    def body(self) -> str:
        return " ".join((self.addr, str(self.seq), *self.tokens))


def encode_frame(start: str, addr: str, seq: int, tokens: list[str] | tuple[str, ...]) -> bytes:
    if start not in (REQUEST_START, REPLY_START):
        raise ValueError(f"bad start char {start!r}")
    if not 0 <= seq <= 255:
        raise ValueError(f"seq out of range: {seq}")
    for token in (addr, *tokens):
        if not token or any(ch in _FORBIDDEN for ch in token) or not token.isascii():
            raise ValueError(f"invalid token {token!r}")
    body = " ".join((addr, str(seq), *tokens))
    raw = f"{start}{body}*{checksum(body):02X}\n".encode("ascii")
    if len(raw) > MAX_FRAME_LEN:
        raise ValueError(f"frame too long ({len(raw)} > {MAX_FRAME_LEN} bytes)")
    return raw


def decode_frame(line: bytes, expect_start: str | None = None) -> Frame:
    """Parse one line. Bytes before the start char are ignored: an idle RS-485
    line without bias resistors often produces a few garbage bytes."""
    text = line.decode("ascii", errors="replace").rstrip("\r\n")
    starts = [i for i in (text.rfind(REQUEST_START), text.rfind(REPLY_START)) if i >= 0]
    if not starts:
        raise FrameError(f"no start char in {text!r}")
    text = text[max(starts):]
    start, rest = text[0], text[1:]
    if expect_start is not None and start != expect_start:
        raise FrameError(f"expected {expect_start!r} frame, got {start!r}")
    if len(rest) < 4 or rest[-3] != "*":
        raise FrameError(f"missing checksum in {text!r}")
    body, cs_hex = rest[:-3], rest[-2:]
    try:
        received = int(cs_hex, 16)
    except ValueError as exc:
        raise FrameError(f"bad checksum digits in {text!r}") from exc
    if received != checksum(body):
        raise FrameError(f"checksum mismatch in {text!r} (expected {checksum(body):02X})")
    parts = body.split(" ")
    if len(parts) < 3 or not parts[1].isdigit():
        raise FrameError(f"malformed frame {text!r}")
    seq = int(parts[1])
    if seq > 255:
        raise FrameError(f"seq out of range in {text!r}")
    return Frame(start=start, addr=parts[0], seq=seq, tokens=tuple(parts[2:]))


@dataclass(frozen=True)
class Reply:
    """A decoded reply: OK with KEY=VALUE fields, or ERR with an error code."""

    addr: str
    seq: int
    ok: bool
    error_code: str | None = None
    fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_frame(cls, frame: Frame) -> "Reply":
        if frame.start != REPLY_START or not frame.tokens:
            raise FrameError(f"not a reply: {frame}")
        head, rest = frame.tokens[0], frame.tokens[1:]
        if head == "OK":
            return cls(frame.addr, frame.seq, True, None, _parse_fields(rest))
        if head == "ERR":
            code = rest[0] if rest else "UNKNOWN"
            return cls(frame.addr, frame.seq, False, code, _parse_fields(rest[1:]))
        raise FrameError(f"reply must start with OK or ERR: {frame}")


def _parse_fields(tokens: tuple[str, ...]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if not sep:
            raise FrameError(f"expected KEY=VALUE, got {token!r}")
        fields[key.upper()] = value
    return fields
