"""Tests for everything except the Bluetooth radio itself.

Run on any computer:  python -m pytest tests
"""

import asyncio
import json

import pytest

from cardforge_setup.auth import AuthManager, hash_password, verify_password
from cardforge_setup.config import MachineIdentity, SetupConfig, machine_identity, read_serial
from cardforge_setup.framing import FrameDecoder, split_frames
from cardforge_setup.network import (
    wifi_readiness,
    CONNECT_FAILED,
    NETWORK_NOT_FOUND,
    TIMEOUT,
    WRONG_PASSWORD,
    NmcliWifi,
    SimulatedWifi,
    classify_nmcli_failure,
    parse_active_ssid,
    parse_terse_ssids,
)
from cardforge_setup.protocol import SetupProtocol

FAST_HASH = hash_password("1234", iterations=1000)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ------------------------------------------------------------------ framing


def test_chunks_join_into_messages():
    decoder = FrameDecoder()
    data = b'{"id":1,"cmd":"ping"}\n{"id":2,"cmd":"info"}\n'
    out = []
    for chunk in [data[i : i + 7] for i in range(0, len(data), 7)]:
        out += decoder.feed(chunk)
    assert out == [b'{"id":1,"cmd":"ping"}', b'{"id":2,"cmd":"info"}']


def test_stale_partial_message_is_dropped():
    clock = Clock()
    decoder = FrameDecoder(stale_after=2, clock=clock)
    assert decoder.feed(b'{"id":1,"cm') == []
    clock.now += 5  # the phone went away mid-message
    assert decoder.feed(b'{"id":2}\n') == [b'{"id":2}']


def test_too_long_message_is_reported_once():
    decoder = FrameDecoder(max_bytes=10)
    assert decoder.feed(b"x" * 30) == []
    assert decoder.feed(b"yy\n{}\n") == [None, b"{}"]


def test_split_frames_adds_newline_and_respects_size():
    chunks = list(split_frames(b"abcdefghij", 4))
    assert chunks == [b"abcd", b"efgh", b"ij\n"]
    assert all(len(c) <= 4 for c in chunks)


# --------------------------------------------------------------------- auth


def test_password_hash_round_trip():
    encoded = hash_password("s3cret", iterations=1000)
    assert encoded.startswith("pbkdf2_sha256$1000$")
    assert verify_password("s3cret", encoded)
    assert not verify_password("S3cret", encoded)
    assert not verify_password("s3cret", "garbage")


def test_lockout_after_too_many_wrong_passwords():
    clock = Clock()
    auth = AuthManager(FAST_HASH, max_attempts=3, lockout_seconds=60, clock=clock)
    assert auth.login("x").attempts_left == 2
    assert auth.login("x").attempts_left == 1
    locked = auth.login("x")
    assert (locked.error, locked.retry_after) == ("LOCKED", 60)
    assert auth.login("1234").error == "LOCKED"  # even the right one
    clock.now += 61
    result = auth.login("1234")
    assert result.ok and auth.is_valid(result.session)


def test_sessions_expire():
    clock = Clock()
    auth = AuthManager(FAST_HASH, clock=clock)
    token = auth.login("1234").session
    assert auth.is_valid(token)
    clock.now += 601
    assert not auth.is_valid(token)
    assert not auth.is_valid(None)


# ------------------------------------------------------------------- config


def test_identity_from_cpu_serial(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("Hardware\t: BCM2835\nSerial\t\t: 00000000a1b2c3d4\nModel\t: Raspberry Pi 3\n")
    serial = read_serial(cpuinfo, tmp_path / "missing")
    assert serial == "00000000a1b2c3d4"
    assert machine_identity(SetupConfig(), serial).name == "CardForge-C3D4"
    assert machine_identity(SetupConfig(name="Bench"), serial).name == "Bench"


def test_identity_from_device_tree_on_plain_debian(tmp_path):
    # Mainline kernels (plain Debian) have no Serial line in /proc/cpuinfo.
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nBogoMIPS\t: 38.40\nFeatures\t: fp asimd\n")
    dt = tmp_path / "serial-number"
    dt.write_bytes(b"00000000a1b2c3d4\x00")
    machine_id = tmp_path / "machine-id"
    machine_id.write_text("2636cc6aa9c14669b885bd02c4856f51\n")
    assert read_serial(cpuinfo, machine_id, dt) == "00000000a1b2c3d4"
    assert read_serial(cpuinfo, machine_id, tmp_path / "none") == "2636cc6aa9c14669b885bd02c4856f51"


def fake_nmcli(running="running", devices="wlan0:wifi:disconnected\neth0:ethernet:connected\n"):
    def run(args):
        if "general" in args:
            return 0, running + "\n"
        return 0, devices
    return run


@pytest.mark.parametrize("running,devices,ok,hint", [
    ("running", "wlan0:wifi:disconnected\n", True, "managed"),
    ("running", "wlan0:wifi:connected\n", True, "managed"),
    ("running", "wlan0:wifi:unmanaged\neth0:ethernet:unmanaged\n", False, "/etc/network/interfaces"),
    ("running", "wlan0:wifi:unavailable\n", False, "rfkill"),
    ("running", "eth0:ethernet:connected\n", False, "firmware"),
    ("asleep", "", False, "not running"),
])
def test_wifi_readiness(running, devices, ok, hint):
    result, message = wifi_readiness("wlan0", run=fake_nmcli(running, devices))
    assert result is ok and hint in message


def test_config_save_and_load(tmp_path):
    path = tmp_path / "cardforge" / "setup.json"
    SetupConfig(password_hash=FAST_HASH, api_port=8080).save(path)
    loaded = SetupConfig.load(path)
    assert (loaded.password_hash, loaded.api_port) == (FAST_HASH, 8080)
    assert oct(path.stat().st_mode & 0o777) == "0o600"


# ------------------------------------------------------------------ network


def test_nmcli_failures_are_classified():
    assert classify_nmcli_failure(3, "Error: Timeout 45 sec expired.") == TIMEOUT
    assert classify_nmcli_failure(10, "Error: No network with SSID 'x' found.") == NETWORK_NOT_FOUND
    assert (
        classify_nmcli_failure(4, "Error: Connection activation failed: Secrets were required, but not provided.")
        == WRONG_PASSWORD
    )
    assert classify_nmcli_failure(4, "Error: Connection activation failed: (7) IP configuration failed.") == CONNECT_FAILED


def test_terse_ssid_list_is_unescaped():
    assert parse_terse_ssids("Home\nCafe\\: 2.4G\n\nHome\n") == ["Home", "Cafe: 2.4G"]


class FakeNmcli:
    def __init__(self, connect_result=(0, "ok"), ssids="Home\n", ip="192.168.0.42/24", active=""):
        self.calls = []
        self.active = active
        self.connect_result = connect_result
        self.ssids = ssids
        self.ip = ip

    async def __call__(self, args, timeout):
        self.calls.append(list(args))
        if "ACTIVE,SSID" in args:
            return 0, self.active
        if "list" in args:
            return 0, self.ssids
        if "connect" in args:
            if isinstance(self.connect_result, list):
                return self.connect_result.pop(0) if len(self.connect_result) > 1 else self.connect_result[0]
            return self.connect_result
        if "show" in args:
            return 0, self.ip
        return 0, ""


def test_nmcli_connect_success_uses_named_profile():
    async def go():
        runner = FakeNmcli()
        wifi = NmcliWifi(runner=runner, retry_delay=0)
        assert wifi.start("Home", "password1")
        assert not wifi.start("Home", "password1")  # busy
        await wifi.wait()
        return wifi, runner

    wifi, runner = asyncio.run(go())
    assert wifi.status.state == "connected"
    connect = next(c for c in runner.calls if "connect" in c)
    assert connect[connect.index("connect") + 1] == "Home"
    assert "name" in connect and "cardforge-wifi" in connect
    assert "hidden" not in connect


def test_nmcli_wrong_password_reports_and_cleans_up():
    async def go():
        runner = FakeNmcli(connect_result=(4, "Secrets were required, but not provided"))
        wifi = NmcliWifi(runner=runner, retry_delay=0)
        wifi.start("Home", "badpassword")
        await wifi.wait()
        return wifi, runner

    wifi, runner = asyncio.run(go())
    assert (wifi.status.state, wifi.status.error) == ("failed", WRONG_PASSWORD)
    assert runner.calls[-1][-3:] == ["delete", "id", "cardforge-wifi"]


def test_nmcli_unknown_network_is_tried_as_hidden_then_not_found():
    async def go():
        runner = FakeNmcli(connect_result=(4, "Activation failed"), ssids="Other\n")
        wifi = NmcliWifi(runner=runner, retry_delay=0)
        wifi.start("Ghost", "")
        await wifi.wait()
        return wifi, runner

    wifi, runner = asyncio.run(go())
    connect = next(c for c in runner.calls if "connect" in c)
    assert "hidden" in connect and "password" not in connect
    assert wifi.status.error == NETWORK_NOT_FOUND


# ----------------------------------------------------------------- protocol


def make_protocol(wifi=None, ip="192.168.0.42", api_ok=True):
    async def ip_lookup():
        return ip

    async def api_check(_port):
        return api_ok

    return SetupProtocol(
        identity=MachineIdentity(machine_id="00000000a1b2c3d4", name="CardForge-C3D4"),
        auth=AuthManager(FAST_HASH, max_attempts=3),
        wifi=wifi or SimulatedWifi(delay=0),
        api_port=8000,
        ip_lookup=ip_lookup,
        api_check=api_check,
    )


def call(protocol, message):
    raw = json.dumps(message).encode() if isinstance(message, dict) else message
    return json.loads(asyncio.run(protocol.handle_raw(raw)))


def test_info_and_ping():
    protocol = make_protocol()
    assert call(protocol, {"id": 1, "cmd": "ping"}) == {"id": 1, "ok": True}
    info = call(protocol, {"id": 2, "cmd": "info"})
    assert info["name"] == "CardForge-C3D4" and info["protocol"] == 1 and info["api_port"] == 8000


def test_bad_requests():
    protocol = make_protocol()
    assert call(protocol, b"not json")["error"] == "BAD_JSON"
    assert call(protocol, b"[1,2]")["error"] == "BAD_JSON"
    assert call(protocol, {"id": 3, "cmd": "reboot"}) == {"id": 3, "ok": False, "error": "BAD_CMD"}
    assert call(protocol, {"id": 4, "cmd": "auth"})["error"] == "BAD_ARG"
    assert json.loads(asyncio.run(protocol.handle_raw(None)))["error"] == "TOO_LONG"


def test_wifi_needs_a_session():
    protocol = make_protocol()
    reply = call(protocol, {"id": 1, "cmd": "wifi", "ssid": "Home", "password": "password1"})
    assert reply["error"] == "NOT_AUTHENTICATED"
    wrong = call(protocol, {"id": 2, "cmd": "auth", "password": "nope"})
    assert (wrong["error"], wrong["attempts_left"]) == ("WRONG_PASSWORD", 2)


def test_full_setup_flow():
    async def go():
        protocol = make_protocol()

        async def send(message):
            return json.loads(await protocol.handle_raw(json.dumps(message).encode()))

        session = (await send({"id": 1, "cmd": "auth", "password": "1234"}))["session"]
        bad = await send({"id": 2, "cmd": "wifi", "session": session, "ssid": "Home", "password": "short"})
        started = await send({"id": 3, "cmd": "wifi", "session": session, "ssid": "Home", "password": "password1"})
        await protocol.wifi.wait()
        status = await send({"id": 4, "cmd": "status"})
        return bad, started, status

    bad, started, status = asyncio.run(go())
    assert (bad["error"], bad["field"]) == ("BAD_ARG", "password")
    assert started == {"id": 3, "ok": True, "state": "connecting"}
    assert status["wifi"] == {"state": "connected", "ssid": "Home", "error": None}
    assert (status["ip"], status["api_port"], status["api_ok"]) == ("192.168.0.42", 8000, True)


def test_status_without_network():
    status = call(make_protocol(ip=None), {"id": 1, "cmd": "status"})
    assert status["ip"] is None and status["api_ok"] is False
    assert status["wifi"]["state"] == "idle"


def test_simulated_network_failure():
    async def go():
        protocol = make_protocol()
        protocol.wifi.start("fail", "")
        await protocol.wifi.wait()
        return protocol.wifi.status

    status = asyncio.run(go())
    assert (status.state, status.error) == ("failed", NETWORK_NOT_FOUND)


@pytest.mark.parametrize("password,ok", [("", True), ("1234567", False), ("12345678", True), ("a" * 63, True),
                                         ("a" * 64, True), ("g" * 64, False), ("a" * 65, False)])
def test_wifi_password_rules(password, ok):
    from cardforge_setup.protocol import _valid_wifi_password

    assert _valid_wifi_password(password) is ok


# --------------------------------------------------------------- BLE glue


class FakeCharacteristic:
    def __init__(self, uuid):
        self.uuid = uuid
        self.value = bytearray()


class FakeBless:
    """Stands in for bless.BlessServer: records every notification.
    `busy` makes the first update_value calls fail, like a full macOS queue."""

    def __init__(self, busy=0):
        self.tx = FakeCharacteristic("tx")
        self.notifications = []
        self.busy = busy

    def get_characteristic(self, uuid):
        return self.tx

    def update_value(self, service_uuid, char_uuid):
        if self.busy:
            self.busy -= 1
            return False
        self.notifications.append(bytes(self.tx.value))
        return True


def test_ble_server_answers_in_small_notifications():
    from cardforge_setup import RX_UUID
    from cardforge_setup.ble_server import BleSetupServer

    async def go():
        server = BleSetupServer(make_protocol(), name="CardForge-C3D4", notify_chunk=20)
        server._server = FakeBless()
        server._loop = asyncio.get_running_loop()
        rx = FakeCharacteristic(RX_UUID)
        request = b'{"id":9,"cmd":"info"}\n'
        for i in range(0, len(request), 5):  # the app writes in MTU-sized chunks
            server._on_write(rx, bytearray(request[i : i + 5]))
        server._on_write(FakeCharacteristic("other"), bytearray(b"ignored\n"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if server._server.notifications and server._server.notifications[-1].endswith(b"\n"):
                break
        return server._server.notifications

    chunks = asyncio.run(go())
    assert all(len(c) <= 20 for c in chunks)
    reply = json.loads(b"".join(chunks))
    assert reply["id"] == 9 and reply["name"] == "CardForge-C3D4"


def test_ble_server_accepts_writes_from_another_thread_and_retries_full_queue():
    """macOS (CoreBluetooth) calls the write callback from its own thread and
    may refuse a notification when its queue is full."""
    import threading

    from cardforge_setup import RX_UUID
    from cardforge_setup.ble_server import BleSetupServer

    async def go():
        server = BleSetupServer(make_protocol(), name="CardForge-C3D4")
        server._server = FakeBless(busy=3)
        server._loop = asyncio.get_running_loop()
        rx = FakeCharacteristic(RX_UUID.upper())  # macOS gives uppercase UUIDs
        request = b'{"id":3,"cmd":"ping"}\n'
        writer = threading.Thread(
            target=lambda: [server._on_write(rx, bytearray(request[i : i + 4])) for i in range(0, len(request), 4)]
        )
        writer.start()
        writer.join()
        for _ in range(200):
            await asyncio.sleep(0.01)
            notes = server._server.notifications
            if notes and notes[-1].endswith(b"\n"):
                break
        return server._server.notifications

    reply = json.loads(b"".join(asyncio.run(go())))
    assert reply == {"id": 3, "ok": True}


def test_active_ssid_is_parsed():
    assert parse_active_ssid("no:Other\nyes:Cafe\\: 2.4G\n") == "Cafe: 2.4G"
    assert parse_active_ssid("no:Other\n") is None


def test_nmcli_already_on_network_keeps_connection():
    async def go():
        runner = FakeNmcli(active="no:Other\nyes:Home\n")
        wifi = NmcliWifi(runner=runner, retry_delay=0)
        wifi.start("Home", "password1")
        await wifi.wait()
        return wifi, runner

    wifi, runner = asyncio.run(go())
    assert wifi.status.state == "connected"
    assert not any("connect" in c or "delete" in c for c in runner.calls)


def test_nmcli_on_other_network_switches():
    async def go():
        runner = FakeNmcli(active="yes:Other\n")
        wifi = NmcliWifi(runner=runner, retry_delay=0)
        wifi.start("Home", "password1")
        await wifi.wait()
        return wifi, runner

    wifi, runner = asyncio.run(go())
    assert wifi.status.state == "connected"
    assert any("connect" in c for c in runner.calls)


def test_nmcli_not_found_after_scan_is_retried():
    async def go():
        runner = FakeNmcli(connect_result=[(10, "Error: No network with SSID 'Home' found."), (0, "ok")])
        wifi = NmcliWifi(runner=runner, retry_delay=0)
        wifi.start("Home", "password1")
        await wifi.wait()
        return wifi, runner

    wifi, runner = asyncio.run(go())
    assert wifi.status.state == "connected"
    connects = [c for c in runner.calls if "connect" in c]
    assert len(connects) == 2
    # the old profile is deleted before the first scan, so the scan is fresh
    first_delete = next(i for i, c in enumerate(runner.calls) if "delete" in c)
    first_scan = next(i for i, c in enumerate(runner.calls) if "SSID" in c)
    assert first_delete < first_scan


def test_nmcli_not_found_gives_up_after_attempts():
    async def go():
        runner = FakeNmcli(connect_result=(10, "Error: No network with SSID 'Home' found."))
        wifi = NmcliWifi(runner=runner, retry_delay=0, connect_attempts=3)
        wifi.start("Home", "password1")
        await wifi.wait()
        return wifi, runner

    wifi, runner = asyncio.run(go())
    assert wifi.status.error == NETWORK_NOT_FOUND
    assert len([c for c in runner.calls if "connect" in c]) == 3


def test_nmcli_wrong_password_is_not_retried():
    async def go():
        runner = FakeNmcli(connect_result=(4, "Secrets were required, but not provided"))
        wifi = NmcliWifi(runner=runner, retry_delay=0)
        wifi.start("Home", "badpassword")
        await wifi.wait()
        return wifi, runner

    wifi, runner = asyncio.run(go())
    assert len([c for c in runner.calls if "connect" in c]) == 1