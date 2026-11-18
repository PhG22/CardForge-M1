"""A card that does not fall when the flap opens is shaken loose by the platform
vibration motor (A1 VIBRATE, a1_top 1.2, motor on D7).

  * falls by itself            -> no vibration at all
  * falls after pulse 1 or 2   -> the run goes on, no fault, no alert
  * still there after pulse 2  -> DROP_FAILED: the run pauses and the app is alerted
  * firmware without VIBRATE   -> DROP_FAILED right away, as before
"""

from fastapi.testclient import TestClient

from sorter_api.api import create_app
from sorter_api.modules import TopModule

from test_integration import ScriptedVision, card, events_until, fast_settings, identified

PULSE_MS = 100


def vib_settings(**overrides):
    base = dict(sim_stack_cards=2, drop_vibration_ms=PULSE_MS, drop_vibration_wait_s=0.05)
    base.update(overrides)
    return fast_settings(**base)


def plains(n=10):
    return ScriptedVision([identified(card("Plains", "W"))] * n)


def vibrate_commands(client):
    return [cmd for cmd in client.app.state.sim_world.top.executed if cmd.startswith("VIBRATE")]


def run_to_end(client):
    """Start a run, return every event until it ends (or pauses)."""
    with client.websocket_connect("/ws/events") as ws:
        ws.receive_json()
        assert client.post("/run/start").status_code == 200
        events = []
        while not events or events[-1]["type"] not in ("run_finished", "fault"):
            events.append(ws.receive_json())
    return events


def test_a_card_that_falls_by_itself_needs_no_vibration():
    with TestClient(create_app(vib_settings(), vision=plains())) as c:
        events = run_to_end(c)
        assert events[-1]["type"] == "run_finished" and events[-1]["run"]["status"] == "completed"
        assert vibrate_commands(c) == []


def test_stuck_card_falls_after_the_first_pulse_and_the_run_goes_on():
    with TestClient(create_app(vib_settings(), vision=plains())) as c:
        c.put("/sim/state", json={"platform_stuck_pulses": 1})
        events = run_to_end(c)
        run = events[-1]["run"]
        assert events[-1]["type"] == "run_finished"
        assert run["status"] == "completed" and run["cards_processed"] == 2 and run["error_code"] is None
        assert not [e for e in events if e["type"] in ("fault", "attention")]       # no alert
        assert vibrate_commands(c) == [f"VIBRATE {PULSE_MS}", "VIBRATE 0"]          # 1 pulse, then off
        state = c.get("/sim/state").json()
        assert state["trays_with_cards"] == [1] and state["cards_dropped"] == 2      # it went to its tray
        assert c.get(f"/runs/{run['run_id']}").json()["scan_counts"]["identified"] == 2


def test_stuck_card_falls_after_the_second_pulse():
    with TestClient(create_app(vib_settings(), vision=plains())) as c:
        c.put("/sim/state", json={"platform_stuck_pulses": 2})
        events = run_to_end(c)
        assert events[-1]["type"] == "run_finished" and events[-1]["run"]["status"] == "completed"
        assert not [e for e in events if e["type"] == "fault"]
        assert vibrate_commands(c) == [f"VIBRATE {PULSE_MS}", f"VIBRATE {PULSE_MS}", "VIBRATE 0"]


def test_card_still_stuck_after_two_pulses_pauses_the_run_with_drop_failed():
    with TestClient(create_app(vib_settings(), vision=plains())) as c:
        c.put("/sim/state", json={"platform_stuck_pulses": 3})
        with c.websocket_connect("/ws/events") as ws:
            ws.receive_json()
            run_id = c.post("/run/start").json()["run"]["run_id"]
            fault = events_until(ws, "fault")[-1]
            assert fault["fault"] == "DROP_FAILED" and fault["time"]                  # RF27 alert
            assert "2 vibration pulse(s)" in fault["message"]
            assert vibrate_commands(c) == [f"VIBRATE {PULSE_MS}"] * 2                 # exactly 2 tries
            assert c.get("/sim/state").json()["vibrating"] is False                  # STOP: motor off

            # The user takes the card out by hand and resumes: the same run goes on.
            c.put("/sim/state", json={"platform_has_card": False, "platform_stuck_pulses": 0})
            assert c.post("/run/resume").status_code == 200
            done = events_until(ws)[-1]["run"]
        assert done["run_id"] == run_id and done["status"] == "completed"
        assert done["cards_processed"] == 1          # the card removed by hand is not recorded


def test_firmware_without_vibrate_fails_the_drop_as_before():
    settings = vib_settings(sim_optional_commands=False)
    with TestClient(create_app(settings, vision=plains())) as c:
        c.put("/sim/state", json={"platform_stuck_pulses": 1})
        events = run_to_end(c)
        assert events[-1]["type"] == "fault" and events[-1]["fault"] == "DROP_FAILED"
        assert events[-1]["message"].startswith("The card did not fall from the platform (stuck?).")
        assert vibrate_commands(c) == ["VIBRATE 100"]          # answered ERR BAD_CMD: feature off


def test_vibration_can_be_turned_off_by_setting():
    with TestClient(create_app(vib_settings(drop_vibration_pulses=0), vision=plains())) as c:
        c.put("/sim/state", json={"platform_stuck_pulses": 1})
        events = run_to_end(c)
        assert events[-1]["type"] == "fault" and events[-1]["fault"] == "DROP_FAILED"
        assert vibrate_commands(c) == []


def test_vibrate_command_range():
    import pytest

    top = TopModule(bus=None)
    with pytest.raises(ValueError):
        top.vibrate(3001)
    with pytest.raises(ValueError):
        top.vibrate(-1)
