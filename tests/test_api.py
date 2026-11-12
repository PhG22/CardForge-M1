"""End-to-end tests: HTTP/WebSocket -> controller -> RS-485 frames -> simulated Arduinos."""

import time

import pytest
from fastapi.testclient import TestClient

from sorter_api.api import create_app
from sorter_api.config import Settings


def fast_settings(**overrides) -> Settings:
    s = Settings(simulate=True, sim_time_scale=0.02, identify_delay_s=0.01, flap_settle_s=0.01,
                 card_fall_s=0.01, poll_interval_s=0.005, base_poll_interval_s=0.005,
                 home_poll_interval_s=0.005, feed_timeout_s=0.5, drop_timeout_s=0.5,
                 sim_stack_cards=3, reply_timeout=0.1, run_db=":memory:", home_on_power_on=False, cleanup_dirs="",
                 base_state_file="")
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


@pytest.fixture
def client():
    with TestClient(create_app(fast_settings())) as c:
        yield c


def wait_until_state(client, state, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/run/status").json()
        if body["state"] == state:
            return body
        time.sleep(0.02)
    raise AssertionError(f"state never became {state}")


def wait_until_idle(client, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/run/status").json()
        if body["state"] == "idle":
            return body
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_full_run_sorts_every_card_and_notifies(client):
    with client.websocket_connect("/ws/events") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        r = client.post("/run/start")
        assert r.status_code == 200, r.text
        assert r.json()["started"] is True

        events = []
        while not events or events[-1]["type"] != "run_finished":
            events.append(ws.receive_json())

    types = [e["type"] for e in events]
    assert types == ["run_started", "card_sorted", "card_sorted", "card_sorted", "run_finished"]
    # RF23: progress only while running, no tray / card identity
    assert all(set(e) == {"type", "run_id", "card_number"} for e in events if e["type"] == "card_sorted")
    finished = events[-1]["run"]
    assert finished["status"] == "completed" and finished["cards_processed"] == 3

    sim = client.get("/sim/state").json()
    assert sim["stack_cards"] == 0 and sim["trays_with_cards"] == [1, 2, 3] and sim["cards_lost"] == 0
    assert sim["flap_angle"] == 80


def test_trays_cycle_back_to_1(client):
    client.put("/sim/state", json={"stack_cards": 8})
    assert client.post("/run/start").status_code == 200
    body = wait_until_idle(client)
    assert body["run"]["status"] == "completed" and body["run"]["cards_processed"] == 8
    assert body["run"]["current_tray"] == 2          # 1..6, then 1, 2


def test_start_refused_when_stack_empty(client):
    client.put("/sim/state", json={"stack_cards": 0})
    r = client.post("/run/start")
    assert r.status_code == 409
    assert [p["code"] for p in r.json()["problems"]] == ["STACK_EMPTY"]
    assert client.get("/run/status").json()["state"] == "idle"


def test_start_refused_reports_every_problem(client):
    client.put("/sim/state", json={"stack_cards": 0, "trays_with_cards": [2, 5], "platform_has_card": True})
    body = client.post("/run/start").json()
    codes = {p["code"]: p for p in body["problems"]}
    assert set(codes) == {"STACK_EMPTY", "TRAYS_NOT_EMPTY", "PLATFORM_NOT_CLEAR"}
    assert codes["TRAYS_NOT_EMPTY"]["trays"] == [2, 5]
    assert "tray 2, 5" in body["message"]


def test_second_run_refused_until_trays_are_emptied(client):
    assert client.post("/run/start").status_code == 200
    wait_until_idle(client)
    client.put("/sim/state", json={"stack_cards": 2})
    r = client.post("/run/start")
    assert r.status_code == 409 and r.json()["problems"][0]["code"] == "TRAYS_NOT_EMPTY"
    client.put("/sim/state", json={"trays_with_cards": []})
    assert client.post("/run/start").status_code == 200


def test_start_while_running_is_refused(client):
    client.put("/sim/state", json={"stack_cards": 50})
    assert client.post("/run/start").status_code == 200
    r = client.post("/run/start")
    assert r.status_code == 409 and r.json()["problems"][0]["code"] == "RUN_ACTIVE"
    client.post("/run/stop")


def test_stop_cancels_the_run(client):
    client.put("/sim/state", json={"stack_cards": 50})
    assert client.post("/run/start").status_code == 200
    time.sleep(0.2)
    r = client.post("/run/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "idle" and body["run"]["status"] == "cancelled"
    assert client.get("/sim/state").json()["stack_cards"] > 0
    assert client.post("/run/stop").status_code == 409     # nothing left to stop


def test_feed_jam_pauses_the_run_and_resume_continues_it(client):
    client.put("/sim/state", json={"top_jammed": True})
    assert client.post("/run/start").status_code == 200
    body = wait_until_state(client, "paused")
    run = body["run"]
    assert run["status"] == "paused" and run["error_code"] == "FEED_TIMEOUT"
    client.put("/sim/state", json={"top_jammed": False})        # the user clears the jam
    r = client.post("/run/resume")
    assert r.status_code == 200 and r.json()["run"]["run_id"] == run["run_id"]
    done = wait_until_idle(client)["run"]
    assert done["run_id"] == run["run_id"] and done["status"] == "completed" and done["cards_processed"] == 3


def test_offline_module_gives_503(client):
    client.put("/sim/state", json={"offline_modules": ["A2"]})
    r = client.post("/run/start")
    assert r.status_code == 503
    assert r.json()["problems"][0]["module"] == "A2"


def test_hardware_status(client):
    body = client.get("/hardware/status").json()
    assert body["top"]["online"] and body["top"]["stack_has_card"] is True
    assert body["bottom"]["online"] and body["bottom"]["trays_with_cards"] == []


# ---------------------------------------------------------------- top-only bench

@pytest.fixture
def top_client():
    with TestClient(create_app(fast_settings(bottom_enabled=False))) as c:
        yield c


def wait_for_sim(client, key, value, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/sim/state").json()[key] == value:
            return
        time.sleep(0.005)
    raise AssertionError(f"sim {key} never became {value}")


def assert_machine_is_safe(client):
    sim = client.get("/sim/state").json()
    assert sim["platform_has_card"] is False      # no card left on the platform
    assert sim["flap_angle"] == 80                # flap closed
    assert sim["roller_running"] is False         # feeder stopped
    return sim


def test_top_only_refuses_empty_stack(top_client):
    top_client.put("/sim/state", json={"stack_cards": 0})
    r = top_client.post("/run/start")
    assert r.status_code == 409
    body = r.json()
    assert body["started"] is False
    assert [p["code"] for p in body["problems"]] == ["STACK_EMPTY"]
    assert "No cards on the stack" in body["message"]


def test_top_only_run_feeds_and_drops_every_card(top_client):
    r = top_client.post("/run/start")
    assert r.status_code == 200 and r.json()["started"] is True
    run = wait_until_idle(top_client)["run"]
    assert run["status"] == "completed" and run["cards_processed"] == 3
    sim = assert_machine_is_safe(top_client)
    assert sim["stack_cards"] == 0 and sim["cards_dropped"] == 3
    assert top_client.get("/hardware/status").json()["bottom"]["enabled"] is False


def test_stack_endpoint(top_client):
    assert top_client.get("/stack").json() == {"has_cards": True, "message": "There are cards on the stack."}
    top_client.put("/sim/state", json={"stack_cards": 0})
    assert top_client.get("/stack").json()["has_cards"] is False


def test_stack_endpoint_when_top_offline(top_client):
    top_client.put("/sim/state", json={"offline_modules": ["A1", "A2"]})
    assert top_client.get("/stack").status_code == 503


def test_stop_while_feeding_finishes_the_card_and_drops_it():
    # slower simulated motors so the stop lands in the middle of a feed
    with TestClient(create_app(fast_settings(bottom_enabled=False, sim_time_scale=0.5))) as c:
        c.put("/sim/state", json={"stack_cards": 10})
        assert c.post("/run/start").status_code == 200
        wait_for_sim(c, "roller_running", True)
        body = c.post("/run/stop").json()
        assert body["state"] == "idle" and body["run"]["status"] == "cancelled"
        assert "card in progress was sorted" in body["message"]
        sim = assert_machine_is_safe(c)
        assert 10 - sim["stack_cards"] == sim["cards_dropped"] == body["run"]["cards_processed"]


def test_stop_while_identifying_drops_the_card_without_waiting():
    with TestClient(create_app(fast_settings(bottom_enabled=False, identify_delay_s=10))) as c:
        with c.websocket_connect("/ws/events") as ws:
            assert c.post("/run/start").status_code == 200
            wait_for_sim(c, "platform_has_card", True)
            t0 = time.monotonic()
            body = c.post("/run/stop").json()
            assert time.monotonic() - t0 < 2.0          # did not sit out the 10 s
            types = []
            while not types or types[-1] != "run_finished":
                types.append(ws.receive_json()["type"])
        assert "card_cleared" in types
        assert body["run"]["status"] == "cancelled" and body["run"]["cards_processed"] == 1
        assert assert_machine_is_safe(c)["cards_dropped"] == 1


def test_immediate_stop_halts_without_clearing():
    with TestClient(create_app(fast_settings(bottom_enabled=False, identify_delay_s=10))) as c:
        assert c.post("/run/start").status_code == 200
        wait_for_sim(c, "platform_has_card", True)
        body = c.post("/run/stop", params={"immediate": "true"}).json()
        assert body["run"]["status"] == "cancelled" and "immediately" in body["message"]
        sim = c.get("/sim/state").json()
        assert sim["platform_has_card"] is True and sim["roller_running"] is False


# ---------------------------------------------------------------- manual base control

def test_base_manual_moves(client):
    assert client.get("/base/status").json()["base"]["homed"] is False
    r = client.post("/base/goto/1")
    assert r.status_code == 409 and r.json()["code"] == "NOT_HOMED"

    r = client.post("/base/home")
    assert r.status_code == 200
    base = r.json()["base"]
    assert base["homed"] and base["position"] == 3 and base["steps"] == 0

    base = client.post("/base/goto/1").json()["base"]
    assert base["position"] == 1 and base["steps"] == 3306
    base = client.post("/base/goto/6").json()["base"]
    assert base["position"] == 6 and base["steps"] == -4960

    base = client.post("/base/move", json={"degrees": 46.4953575}).json()["base"]
    assert base["steps"] == 1653 and base["position"] == 2      # exactly tray 2
    r = client.post("/base/move", json={"steps": 1000})
    assert r.json()["base"]["position"] == 0 and "1000 steps from home" in r.json()["message"]


def test_base_move_validation(client):
    assert client.post("/base/move", json={}).status_code == 400
    assert client.post("/base/move", json={"steps": 1, "degrees": 1}).status_code == 400
    assert client.post("/base/move", json={"steps": 99999}).status_code == 400
    assert client.post("/base/goto/7").status_code == 400


def test_base_endpoints_refused_during_a_run(client):
    client.put("/sim/state", json={"stack_cards": 50})
    assert client.post("/run/start").status_code == 200
    r = client.post("/base/goto/2")
    assert r.status_code == 409 and r.json()["code"] == "RUN_ACTIVE"
    client.post("/run/stop")


def test_base_endpoints_refused_in_top_only_mode(top_client):
    r = top_client.post("/base/home")
    assert r.status_code == 409 and r.json()["code"] == "BOTTOM_DISABLED"


def test_run_after_manual_moves_homes_again(client):
    client.post("/base/home")
    client.post("/base/move", json={"steps": -2000})
    assert client.post("/run/start").status_code == 200      # start homes the base again
    run = wait_until_idle(client)["run"]
    assert run["status"] == "completed"
    assert client.get("/sim/state").json()["trays_with_cards"] == [1, 2, 3]


def test_api_leaves_the_base_alone_while_it_moves():
    # real-ish timing: the move takes ~0.5 s and the API must not poll A2 during it
    with TestClient(create_app(fast_settings(sim_time_scale=0.5))) as c:
        c.post("/base/home")
        world = c.app.state.sim_world
        world.bottom.executed.clear()
        base = c.post("/base/goto/1").json()["base"]
        assert base["position"] == 1 and base["drift"] == 0
        cmds = world.bottom.executed
        assert cmds[0] == "GOTO 1"
        assert cmds[1:] == ["STATUS"]        # one check after the reported time, nothing during the move


# ---------------------------------------------------------------- RF08: homing at power-on

def test_base_is_homed_when_the_service_starts():
    with TestClient(create_app(fast_settings(home_on_power_on=True))) as c:
        deadline = time.monotonic() + 5
        while not c.get("/sim/state").json()["base_homed"]:
            assert time.monotonic() < deadline, "the base was not homed at power-on"
            time.sleep(0.01)
        wait_until_idle(c)
        assert c.get("/base/status").json()["base"]["position"] == 3
        assert c.post("/run/start").status_code == 200          # Start works (and homes again)


def test_start_during_power_on_homing_says_so():
    with TestClient(create_app(fast_settings(home_on_power_on=True, sim_time_scale=0.3))) as c:
        r = c.post("/run/start")
        assert r.status_code == 409
        assert r.json()["problems"][0]["code"] == "HOMING"
        wait_until_idle(c, timeout=20)
        assert c.get("/sim/state").json()["base_homed"] is True
        assert c.post("/run/start").status_code == 200


def test_top_only_bench_does_not_home_at_power_on():
    with TestClient(create_app(fast_settings(home_on_power_on=True, bottom_enabled=False))) as c:
        assert c.get("/run/status").json()["state"] == "idle"
        assert c.post("/run/start").status_code == 200


def test_service_starts_even_if_the_base_does_not_answer_at_power_on():
    from sorter_api.sim import SimSerialPort, SimWorld

    world = SimWorld(stack_cards=2, time_scale=0.02)
    world.set_state(offline_modules=["A2"])
    settings = fast_settings(home_on_power_on=True)
    with TestClient(create_app(settings, serial_port=SimSerialPort(world))) as c:
        wait_until_idle(c)                                        # homing gave up, the API answers
        r = c.post("/run/start")
        assert r.status_code == 503 and r.json()["problems"][0]["code"] == "MODULE_OFFLINE"
