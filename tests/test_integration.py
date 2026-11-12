"""Integration tests of the Pi main loop: simulated A1/A2 + vision + run.db + the app's routes.

Most tests use a scripted vision double, so they run fast and know which card is
on the platform. The last one runs the real computer vision on photos from
input_cartas against data/cards.db (skipped if they are missing).
"""

import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cardforge.cv import IdentificationResult, IdentificationStatus, MatchMethod
from cardforge.data.records import CardRecord
from sorter_api.api import create_app
from sorter_api.config import Settings
from sorter_api.controller import SorterController
from sorter_api.store import RunStore

ROOT = Path(__file__).resolve().parents[1]


def fast_settings(**overrides) -> Settings:
    s = Settings(simulate=True, sim_time_scale=0.02, flap_settle_s=0.01, card_fall_s=0.01, poll_interval_s=0.005,
                 base_poll_interval_s=0.005, home_poll_interval_s=0.005, feed_timeout_s=0.5, drop_timeout_s=0.5,
                 sim_stack_cards=3, reply_timeout=0.1, run_db=":memory:", home_on_power_on=False,
                 # never touch the repository's own folders from the tests
                 cleanup_dirs="", crop_captures_dir=tempfile.mkdtemp(prefix="calibragem_"))
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def card(name, colors, set_code="FIN", number="1"):
    return CardRecord(print_id=f"id-{name}", name=name, mana_cost="", type_line="Creature",
                      color_identity=frozenset(colors), rarity="common", set_code=set_code, collector_number=number)


def identified(c):
    return IdentificationResult(IdentificationStatus.IDENTIFIED, card=c, method=MatchMethod.SET_NUMBER, confidence=100)


LOW = IdentificationResult(IdentificationStatus.LOW_CONFIDENCE, method=MatchMethod.NONE, confidence=0)
FACE_DOWN = IdentificationResult(IdentificationStatus.FACE_DOWN)


class ScriptedVision:
    """Stands in for cardforge.cv.VisionModule: returns the next scripted result per capture."""

    ready = True

    def __init__(self, results, delay_s=0.0):
        self.results = list(results)
        self.calls = 0
        self.delay_s = delay_s

    def identify(self):
        self.calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        return self.results.pop(0) if self.results else LOW


def wait_state(client, state, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/run/status").json()
        if body["state"] == state:
            return body
        time.sleep(0.02)
    raise AssertionError(f"state never became {state}: {client.get('/run/status').json()}")


def events_until(ws, last="run_finished"):
    events = []
    while not events or events[-1]["type"] != last:
        events.append(ws.receive_json())
    return events


# ------------------------------------------------------------------ sorting with vision

def test_cards_go_to_their_color_tray_and_are_recorded():
    # white -> 1, multicolor -> 6, unidentified after 3 captures -> 6, face-down -> 6, green -> 5
    vision = ScriptedVision([identified(card("Plains", "W")), identified(card("Tifa", "RGW")),
                             LOW, LOW, LOW, FACE_DOWN, identified(card("Forest", "G"))])
    with TestClient(create_app(fast_settings(sim_stack_cards=5), vision=vision)) as c:
        with c.websocket_connect("/ws/events") as ws:
            ws.receive_json()
            assert c.post("/run/start").status_code == 200
            events = events_until(ws)
        run = events[-1]["run"]
        assert run["status"] == "completed" and run["cards_processed"] == 5
        assert vision.calls == 7                                    # 1 + 1 + 3 (RF29) + 1 + 1
        assert c.get("/sim/state").json()["trays_with_cards"] == [1, 5, 6]

        # RF23: during the run, only progress and attention events; no identity, no tray
        sorted_events = [e for e in events if e["type"] == "card_sorted"]
        assert [e["card_number"] for e in sorted_events] == [1, 2, 3, 4, 5]
        assert all("tray" not in e and "card_identifier" not in e for e in sorted_events)
        assert [e["kind"] for e in events if e["type"] == "attention"] == ["unidentified", "face_down"]

        # RF22: results after the run
        res = c.get(f"/runs/{run['run_id']}").json()
        assert res["bin_counts"] == {"1": 1, "6": 3, "5": 1}
        assert res["scan_counts"] == {"identified": 3, "unidentified": 1, "face_down": 1}
        cards = res["cards"]
        assert [(x["card_name"], x["bin"], x["scan_status"]) for x in cards] == [
            ("Plains", 1, "identified"), ("Tifa", 6, "identified"), (None, 6, "unidentified"),
            (None, 6, "face_down"), ("Forest", 5, "identified")]
        assert cards[2]["attempts"] == 3 and cards[0]["card_identifier"] == "id-Plains"
        assert res["run"]["successful_scans"] == 3 and res["run"]["failed_scans"] == 2


def test_results_are_hidden_while_the_run_is_active():
    vision = ScriptedVision([identified(card("Plains", "W"))] * 10, delay_s=0.05)
    with TestClient(create_app(fast_settings(sim_stack_cards=10), vision=vision)) as c:
        run_id = c.post("/run/start").json()["run"]["run_id"]
        r = c.get(f"/runs/{run_id}")
        assert r.status_code == 409 and "cards" not in r.json()
        assert c.get("/run/status").json()["run"]["current_tray"] is None
        c.post("/run/stop")
        assert c.get(f"/runs/{run_id}").status_code == 200


def test_batch_ends_at_the_card_limit():
    vision = ScriptedVision([identified(card("Plains", "W"))] * 10)
    with TestClient(create_app(fast_settings(sim_stack_cards=5, max_cards_per_run=2), vision=vision)) as c:
        assert c.post("/run/start").status_code == 200
        run = wait_state(c, "idle")["run"]
        assert run["status"] == "completed" and run["cards_processed"] == 2 and run["error_code"] == "BATCH_LIMIT"
        assert c.get("/sim/state").json()["stack_cards"] == 3


def test_stop_during_identification_still_sorts_the_identified_card():
    # identified, then the stop arrives before the base moves: the card goes to its own tray (G -> 5)
    vision = ScriptedVision([identified(card("Forest", "G"))], delay_s=0.4)
    with TestClient(create_app(fast_settings(sim_stack_cards=5), vision=vision)) as c:
        assert c.post("/run/start").status_code == 200
        time.sleep(0.2)                                             # inside the 0.4 s capture
        body = c.post("/run/stop").json()
        assert body["run"]["status"] == "cancelled" and body["run"]["cards_processed"] == 1
        assert c.get("/sim/state").json()["trays_with_cards"] == [5]
        assert c.get(f"/runs/{body['run']['run_id']}").json()["cards"][0]["card_name"] == "Forest"


def test_jam_pauses_alerts_and_resume_keeps_the_same_run():
    vision = ScriptedVision([identified(card("Plains", "W"))] * 10)
    with TestClient(create_app(fast_settings(sim_stack_cards=3), vision=vision)) as c:
        with c.websocket_connect("/ws/events") as ws:
            ws.receive_json()
            c.put("/sim/state", json={"top_jammed": True})
            run_id = c.post("/run/start").json()["run"]["run_id"]
            fault = events_until(ws, "fault")[-1]
            assert fault["fault"] == "FEED_TIMEOUT" and fault["time"]          # RF27: type + timestamp
            assert c.get("/sim/state").json()["status_led"] == "FEED_JAM"      # RF16
            assert c.post("/run/start").json()["problems"][0]["code"] == "RUN_ACTIVE"
            c.put("/sim/state", json={"top_jammed": False})
            assert c.post("/run/resume").status_code == 200
            done = events_until(ws)[-1]["run"]
        assert done["run_id"] == run_id and done["status"] == "completed" and done["cards_processed"] == 3
        assert c.get("/sim/state").json()["status_led"] == "IDLE"


def test_stop_while_paused_closes_the_run():
    with TestClient(create_app(fast_settings(), vision=ScriptedVision([]))) as c:
        c.put("/sim/state", json={"top_jammed": True})
        c.post("/run/start")
        wait_state(c, "paused")
        body = c.post("/run/stop").json()
        assert body["state"] == "idle" and body["run"]["status"] == "cancelled"
        assert c.post("/run/resume").status_code == 409


def test_retries_raise_the_chamber_light():
    with TestClient(create_app(fast_settings(sim_stack_cards=1), vision=ScriptedVision([LOW, LOW, LOW]))) as c:
        c.post("/run/start")
        wait_state(c, "idle")
        lights = [cmd for cmd in c.app.state.sim_world.top.executed if cmd.startswith("LIGHT")]
        assert lights[-3:] == ["LIGHT 80", "LIGHT 100", "LIGHT 60"]            # RNF09, then back to base


def test_firmware_without_light_and_led_still_sorts():
    vision = ScriptedVision([LOW] * 9)
    with TestClient(create_app(fast_settings(sim_optional_commands=False), vision=vision)) as c:
        assert c.post("/run/start").status_code == 200
        run = wait_state(c, "idle")["run"]
        assert run["status"] == "completed" and run["cards_processed"] == 3


def test_missing_card_database_blocks_the_start():
    vision = ScriptedVision([])
    vision.ready = False
    with TestClient(create_app(fast_settings(), vision=vision)) as c:
        r = c.post("/run/start")
        assert r.status_code == 409 and r.json()["problems"][0]["code"] == "CARD_DB_MISSING"


def test_quick_refusal_does_not_wait_for_homing():
    with TestClient(create_app(fast_settings(sim_time_scale=1.0), vision=ScriptedVision([]))) as c:
        c.put("/sim/state", json={"stack_cards": 0})
        t0 = time.monotonic()
        assert c.post("/run/start").status_code == 409
        assert time.monotonic() - t0 < 1.0                                     # homing takes 2 s here
        assert c.app.state.sim_world.bottom.homed is False


# ------------------------------------------------------------------ persistence

def test_run_interrupted_by_a_restart_is_closed(tmp_path):
    store = RunStore(tmp_path / "run.db")
    store.create_run("r1", "2026-11-07T10:00:00.000Z")
    store.close()
    store = RunStore(tmp_path / "run.db")
    SorterController(None, None, None, None, fast_settings(), store)        # boot
    run = store.get_run("r1")
    assert run["status"] == "failed" and run["error_code"] == "INTERRUPTED"


def test_csv_export_follows_rfc4180():
    vision = ScriptedVision([identified(card('Fire, "Ice"', "UR"))])
    with TestClient(create_app(fast_settings(sim_stack_cards=1), vision=vision)) as c:
        c.post("/run/start")
        wait_state(c, "idle")
        r = c.get("/inventory/export.csv")
        assert r.headers["content-type"].startswith("text/csv")
        lines = r.text.split("\r\n")
        assert lines[0].startswith("run_uuid,run_started_at,card_number,scan_status,bin,card_name")
        assert '"Fire, ""Ice"""' in lines[1]


# ------------------------------------------------------------------ the mobile app's routes

def app_login(c, password="x"):
    r = c.post("/auth/login", json={"identifier": "admin", "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"} if r.status_code == 200 else r


def test_app_contract_start_status_stop_and_cards():
    vision = ScriptedVision([identified(card("Plains", "W")), identified(card("Plains", "W")),
                             identified(card("Forest", "G"))])
    with TestClient(create_app(fast_settings(), vision=vision)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/cards").status_code == 401                              # needs the token
        h = app_login(c)
        machine = "/machines/B8%3A27%3AEB%3A00%3AA1%3AB2"
        assert c.get(f"{machine}/status", headers=h).json() == {"state": "idle"}
        assert c.post(f"{machine}/start", headers=h).json()["state"] in ("running", "idle")
        wait_state(c, "idle")
        assert c.get(f"{machine}/status", headers=h).json() == {"state": "idle"}
        assert c.post(f"{machine}/stop", headers=h).json() == {"state": "idle"}  # nothing to stop

        page = c.get("/cards", params={"limit": 30, "offset": 0}, headers=h).json()
        assert page["total"] == 2
        assert page["items"][0] == {"id": "id-Forest", "name": "Forest", "folders": [{"folder": 5, "quantity": 1}]}
        assert page["items"][1]["folders"] == [{"folder": 1, "quantity": 2}]
        assert c.get("/cards", params={"query": "FOR"}, headers=h).json()["total"] == 1


def test_app_start_refusal_and_resume_from_stopped():
    with TestClient(create_app(fast_settings(), vision=ScriptedVision([identified(card("Plains", "W"))] * 5))) as c:
        h = app_login(c)
        c.put("/sim/state", json={"stack_cards": 0})
        r = c.post("/machines/m/start", headers=h)
        assert r.status_code == 409 and r.json()["problems"][0]["code"] == "STACK_EMPTY"

        c.put("/sim/state", json={"stack_cards": 2, "top_jammed": True})
        c.post("/machines/m/start", headers=h)
        wait_state(c, "paused")
        assert c.get("/machines/m/status", headers=h).json() == {"state": "stopped"}   # paused = "stopped"
        c.put("/sim/state", json={"top_jammed": False})
        assert c.post("/machines/m/start", headers=h).json()["state"] in ("running", "idle")  # = resume
        run = wait_state(c, "idle")["run"]
        assert run["status"] == "completed" and run["cards_processed"] == 2


def test_app_password():
    with TestClient(create_app(fast_settings(app_password="s3cret"), vision=ScriptedVision([]))) as c:
        assert app_login(c, "wrong").status_code == 401
        assert c.get("/machines/m/status", headers=app_login(c, "s3cret")).status_code == 200
        assert c.get("/machines/m/status", headers={"Authorization": "Bearer nope"}).status_code == 401


# ------------------------------------------------------------------ real computer vision

SAMPLES = {  # photo -> (card, tray): Forest (G) -> 5, Chaos Warp (R) -> 4, Tifa (RGW) -> 6
    "20260925_230815.jpg": ("Forest", 5),
    "20260925_153311.jpg": ("Chaos Warp", 4),
    "20260925_230528.jpg": ("Tifa, Martial Artist", 6),
}


@pytest.mark.skipif(not (ROOT / "data/cards.db").exists()
                    or not all((ROOT / "input_cartas" / f).exists() for f in SAMPLES),
                    reason="needs data/cards.db and the sample photos")
def test_real_vision_on_sample_photos(tmp_path):
    images = tmp_path / "imgs"
    images.mkdir()
    for i, f in enumerate(SAMPLES):                       # file order = feed order
        (images / f"{i}.jpg").write_bytes((ROOT / "input_cartas" / f).read_bytes())
    settings = fast_settings(sim_stack_cards=3, vision="files", vision_images=str(images),
                             vision_config=str(ROOT / "vision.toml"))
    with TestClient(create_app(settings)) as c:
        assert c.post("/run/start").status_code == 200
        run = wait_state(c, "idle", timeout=60)["run"]
        assert run["status"] == "completed"
        cards = c.get(f"/runs/{run['run_id']}").json()["cards"]
        assert [(x["card_name"], x["bin"]) for x in cards] == list(SAMPLES.values())
        assert c.get("/sim/state").json()["trays_with_cards"] == [4, 5, 6]
