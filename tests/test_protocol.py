import pytest

from sorter_api.bus import ModuleError, ModuleNotResponding, RS485Bus
from sorter_api.protocol import FrameError, Reply, checksum, decode_frame, encode_frame
from sorter_api.sim import SimSerialPort, SimWorld


def test_encode_matches_spec_example():
    assert encode_frame(">", "A1", 17, ["FEED", "4000"]) == b">A1 17 FEED 4000*50\n"
    assert checksum("A1 17 OK") == 0x72


def test_round_trip_reply():
    raw = encode_frame("<", "A2", 18, "OK STATE=IDLE HOMED=1 POS=1 ZERO=1 TRAYS=000100".split())
    reply = Reply.from_frame(decode_frame(raw))
    assert reply.ok and reply.addr == "A2" and reply.seq == 18
    assert reply.fields["TRAYS"] == "000100"


def test_error_reply():
    reply = Reply.from_frame(decode_frame(b"<A1 17 ERR BUSY*0E\n"))
    assert not reply.ok and reply.error_code == "BUSY"


def test_garbage_before_frame_is_ignored():
    assert decode_frame(b"\x00\xff~<A1 17 OK*72\n").addr == "A1"


@pytest.mark.parametrize("line", [
    b"<A1 17 OK*73\n",        # wrong checksum
    b"<A1 17 OK\n",           # no checksum
    b"A1 17 OK*72\n",         # no start char
    b"<A1 x OK*00\n",         # seq not a number
])
def test_bad_frames_rejected(line):
    with pytest.raises(FrameError):
        decode_frame(line)


def test_tokens_cannot_break_framing():
    with pytest.raises(ValueError):
        encode_frame(">", "A1", 1, ["FLAP", "8*0"])


def _bus(world, **kw):
    return RS485Bus(SimSerialPort(world), baudrate=9600, reply_timeout=0.1, **kw)


def test_lost_reply_is_retried_without_executing_twice():
    world = SimWorld(stack_cards=3, time_scale=0.01)
    bus = _bus(world)
    world.drop_replies = 1                    # first reply never reaches the Pi
    assert bus.transact("A1", "FEED", 4000).ok
    assert world.top.executed.count("FEED 4000") == 1


def test_offline_module_raises():
    world = SimWorld()
    world.offline = {"A2"}
    with pytest.raises(ModuleNotResponding):
        _bus(world, retries=1).transact("A2", "STATUS")


def test_err_reply_raises_module_error():
    with pytest.raises(ModuleError) as info:
        _bus(SimWorld()).transact("A2", "GOTO", 3)   # not homed yet
    assert info.value.code == "NOT_HOMED"


def test_rts_direction_pin_drives_de_high_only_while_sending():
    from sorter_api.bus import RtsDirectionPin

    class Port(SimSerialPort):
        rts = None
        log = []
        def write(self, data):
            self.log.append(("write", self.rts))
            return super().write(data)

    port = Port(SimWorld())
    bus = RS485Bus(port, baudrate=9600, reply_timeout=0.1, direction_pin=RtsDirectionPin(port))
    assert port.rts is True                       # idle: RTS asserted = pin LOW = receive
    bus.transact("A1", "PING")
    assert port.log == [("write", False)]         # sending: pin HIGH = DE on
    assert port.rts is True                       # back to receive
