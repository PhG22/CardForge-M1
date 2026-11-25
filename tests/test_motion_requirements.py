"""RF05 (two pull attempts, then Feed Jam), RF08 (homing within 5 s: the saved base position
and parking at the home tray) and RNF10 (3 retransmissions before the link is lost), on the
Pi side with the simulated modules. The firmware side is in a1_top.ino / a2_base.ino."""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from sorter_api.api import create_app
from sorter_api.bus import ModuleNotResponding, RS485Bus
from sorter_api.config import Settings
from sorter_api.sim import SimSerialPort, SimWorld
from test_integration import ScriptedVision, card, fast_settings, identified, wait_state


def actions(device):
    """The commands a simulated module executed, without the STATUS polls."""
    return [c for c in device.executed if not c.startswith(("STATUS", "PING"))]


def wait_homed(client, timeout=10.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        bottom = client.get("/hardware/status").json()["bottom"]
        if bottom.get("homed") and bottom.get("state") == "IDLE":
            return bottom
        time.sleep(0.02)
    raise AssertionError("the base never finished homing")


# ------------------------------------------------------------------ RF05

def test_a_jam_is_declared_after_the_second_pull_attempt():
    with TestClient(create_app(fast_settings(sim_stack_cards=2), vision=ScriptedVision([]))) as c:
        c.put("/sim/state", json={"top_jammed": True})
        c.post("/run/start")
        body = wait_state(c, "paused")
        assert body["run"]["error_code"] == "FEED_TIMEOUT"
        assert "2 pull attempts" in body["run"]["message"]
        top = c.get("/hardware/status").json()["top"]
        assert top["feed_attempt"] == 2 and top["roller_running"] is False
        # Both attempts happen inside one FEED command on the Arduino.
        assert [a for a in actions(c.app.state.sim_world.top) if a.startswith("FEED")] == ["FEED 500"]


def test_a_card_that_arrives_needs_only_the_first_attempt():
    vision = ScriptedVision([identified(card("Plains", "W"))])
    with TestClient(create_app(fast_settings(sim_stack_cards=1), vision=vision)) as c:
        c.post("/run/start")
        wait_state(c, "idle")
        assert c.get("/hardware/status").json()["top"]["feed_attempt"] == 1


# ------------------------------------------------------------------ RNF10

def test_the_link_is_lost_after_three_retransmissions():
    class CountingPort(SimSerialPort):
        def __init__(self, world):
            super().__init__(world)
            self.frames = 0

        def write(self, data):
            self.frames += 1
            return super().write(data)

    world = SimWorld()
    world.offline = {"A2"}
    port = CountingPort(world)
    bus = RS485Bus(port, baudrate=9600, reply_timeout=0.05, retries=Settings().retries)
    with pytest.raises(ModuleNotResponding):
        bus.transact("A2", "STATUS")
    assert Settings().retries == 3
    assert port.frames == 4                      # the request + 3 retransmissions


# ------------------------------------------------------------------ RF08

def test_after_a_run_the_base_parks_over_the_strip_and_its_position_is_saved(tmp_path):
    state = tmp_path / "base_position.json"
    vision = ScriptedVision([identified(card("Lightning Bolt", "R")), identified(card("Island", "U"))])
    with TestClient(create_app(fast_settings(sim_stack_cards=1, base_state_file=str(state)), vision=vision)) as c:
        c.post("/run/start")
        wait_state(c, "idle")
        bottom = c.get("/hardware/status").json()["bottom"]
        assert (bottom["position"], bottom["steps"]) == (3, 0)        # parked at the home tray
        assert c.get("/sim/state").json()["trays_with_cards"] == [4]  # the card is still in its tray
        assert json.loads(state.read_text())["steps"] == 0

        c.put("/sim/state", json={"stack_cards": 1, "trays_with_cards": []})
        c.post("/run/start")
        wait_state(c, "idle")
        moves = actions(c.app.state.sim_world.bottom)
        # The second Start finds the base homed: no saved position needed.
        assert [m for m in moves if m.startswith("HOME")] == ["HOME 40000", "HOME 40000"]
        assert moves[-1] == "GOTO 3"


def test_power_on_homing_starts_from_the_saved_position(tmp_path):
    state = tmp_path / "base_position.json"
    state.write_text(json.dumps({"steps": -1653, "at": "2026-11-11T20:00:00.000Z"}))
    with TestClient(create_app(fast_settings(home_on_power_on=True, base_state_file=str(state)))) as c:
        wait_homed(c)
        assert actions(c.app.state.sim_world.bottom)[0] == "HOME 40000 -1653"
    assert json.loads(state.read_text())["steps"] == 0


@pytest.mark.parametrize("content", ["not json", json.dumps({"steps": "far"}), json.dumps({"steps": 99999})])
def test_a_missing_or_bad_saved_position_means_a_full_search(tmp_path, content):
    state = tmp_path / "base_position.json"
    state.write_text(content)
    with TestClient(create_app(fast_settings(home_on_power_on=True, base_state_file=str(state)))) as c:
        wait_homed(c)
        assert actions(c.app.state.sim_world.bottom)[0] == "HOME 40000"


def test_parking_can_be_turned_off(tmp_path):
    vision = ScriptedVision([identified(card("Lightning Bolt", "R"))])
    settings = fast_settings(sim_stack_cards=1, park_base_after_run=False)
    with TestClient(create_app(settings, vision=vision)) as c:
        c.post("/run/start")
        wait_state(c, "idle")
        assert c.get("/hardware/status").json()["bottom"]["position"] == 4


# ------------------------------------------------------------------ RNF22 / RF02 / RF15

def with_photo_time(result, ms):
    """A copy of the scripted result with the photo time VisionModule.identify() adds."""
    timed = copy.copy(result)
    timed.debug = {**result.debug, "capture_ms": ms}
    return timed


def test_the_run_reports_its_throughput_and_the_slowest_capture():
    from test_integration import LOW
    vision = ScriptedVision([
        with_photo_time(identified(card("Plains", "W")), 120),
        with_photo_time(identified(card("Island", "U")), 300),
        with_photo_time(LOW, 150), with_photo_time(identified(card("Swamp", "B")), 180),   # one retry
    ])
    with TestClient(create_app(fast_settings(sim_stack_cards=3), vision=vision)) as c:
        c.post("/run/start")
        body = wait_state(c, "idle")
        timing = body["timing"]
        assert timing["cards"] == 3 and timing["nominal_cards"] == 2       # the retried card is not nominal
        assert timing["cards_per_min"] > 0 and timing["seconds_per_card"] > 0
        assert timing["nominal_cards_per_min"] > 0
        assert 0.3 <= timing["slowest_capture_s"] < 2.0                    # RF02: the 300 ms photo
        last = timing["last_card"]
        assert last["card"] == 3 and {"feed_s", "identify_s", "capture_s", "move_s", "drop_s", "total_s"} <= set(last)
        assert "cards/min" in body["run"]["message"]
        assert "without retries" in body["run"]["message"]


def test_the_timing_starts_again_with_each_run():
    with TestClient(create_app(fast_settings(sim_stack_cards=2), vision=ScriptedVision([]))) as c:
        c.post("/run/start")
        assert wait_state(c, "idle")["timing"]["cards"] == 2
        c.put("/sim/state", json={"stack_cards": 1, "trays_with_cards": []})
        c.post("/run/start")
        assert wait_state(c, "idle")["timing"]["cards"] == 1
