"""Card database (cards.db): checked at power-on, updated when the user asks (the app).

Power-on: only a check, in the background; the machine gets ready as usual and the app is told when
an update is available. The user starts the update (POST /card-db/update); it runs in the background
with progress and ETA, the status LED blinking white and Start refused; it can be cancelled. No network
/ Scryfall unreachable / any failure: the local cards.db is used.
"""

import sqlite3
import threading
import time

import pytest
import requests
from fastapi.testclient import TestClient

from cardforge.cv.config import VisionConfig
from cardforge.data.repository import DatabaseUnavailable
from cardforge.data.sync import (BulkEntry, ScryfallSyncService, SyncCancelled, SyncCheck, SyncReport,
                                 sets_fingerprint)
from sorter_api.api import create_app
from sorter_api.card_db_update import CardDbBusy, CardDbManager, UpdateProgress
from sorter_api.controller import ManualRefused
from sorter_api.status_led import NET_DOWN, NET_OK, NET_SETUP, MemoryLedDriver, StatusLed
from test_integration import LOW, ScriptedVision, card, fast_settings, identified, wait_state
from test_printed_names import DEFAULT_CARDS, SETS, fake_scryfall

BULK = BulkEntry("2026-11-17T10:00:00+00:00", "https://x/default.jsonl.gz", 81_000_000)
ALL = BulkEntry("2026-11-17T10:20:00+00:00", "https://x/all.jsonl.gz", 408_000_000)
UP_TO_DATE = SyncCheck(True, "up to date", BULK, localized_bulk=ALL)
NEW_CARDS = SyncCheck(False, "new cards or sets on Scryfall", BULK, localized_bulk=ALL)


# ------------------------------------------------------------------ status LED

def test_updating_blinks_white_above_everything():
    driver = MemoryLedDriver()
    led = StatusLed(driver)
    led.set_updating(True)
    assert led.mode == "UPDATING" and (driver.color, driver.blink_hz) == ((1, 1, 1), 1.0)
    led.ready()
    assert led.mode == "UPDATING"
    led.set_updating(False)
    assert led.mode == "IDLE"
    assert led.history == ["STARTING", "UPDATING", "IDLE"]


# ------------------------------------------------------------------ what "up to date" means

def scryfall_sets(**card_counts):
    sets = [{"code": "lci", "name": "LCI", "set_type": "expansion", "card_count": 291},
            {"code": "iko", "name": "IKO", "set_type": "expansion", "card_count": 274},
            {"code": "m21", "name": "M21", "set_type": "core", "card_count": 397},
            {"code": "ymid", "name": "Alchemy", "set_type": "alchemy", "card_count": 30, "digital": True}]
    for s in sets:
        s["card_count"] = card_counts.get(s["code"], s["card_count"])
    return sets


def test_new_cards_not_a_new_bulk_file_make_an_update(tmp_path, monkeypatch):
    fetched = []
    fake_scryfall(monkeypatch, fetched)
    db = tmp_path / "cards.db"
    service = ScryfallSyncService(db, printed_langs=["pt"])
    sets = {"now": scryfall_sets()}
    monkeypatch.setattr(ScryfallSyncService, "fetch_sets", lambda self: sets["now"])

    first = service.check()
    assert not first.current and first.reason == "no local card database"
    assert first.download_bytes == first.bulk.size + first.localized_bulk.size > 0
    service.sync(check=first)
    assert service.check().current
    assert service.local_metadata()["sets_fingerprint"] == sets_fingerprint(sets["now"])

    sets["now"] = scryfall_sets(ymid=31)                       # digital-only set: not on the machine
    assert service.check().current
    sets["now"] = scryfall_sets(lci=292)                       # one new card
    check = service.check()
    assert not check.current and check.reason == "new cards or sets on Scryfall"

    fetched.clear()
    assert service.sync(check=check).updated                   # reuses the check: no index or /sets again
    assert not any(u.endswith("/bulk-data") or u.endswith("/sets") for u in fetched)
    assert service.check().current


def test_languages_schema_and_age_also_count(tmp_path, monkeypatch):
    fake_scryfall(monkeypatch, [], updated_at="2026-11-17T10:00:00+00:00")
    db = tmp_path / "cards.db"
    ScryfallSyncService(db, printed_langs=["pt"]).sync()
    assert "languages" in ScryfallSyncService(db, printed_langs=[]).check().reason

    fake_scryfall(monkeypatch, [], updated_at="2027-01-10T10:00:00+00:00")   # same cards, 54 days later
    assert ScryfallSyncService(db, printed_langs=["pt"]).check().reason == "local cards older than 30 days"
    assert ScryfallSyncService(db, printed_langs=["pt"], max_age_days=0).check().current

    conn = sqlite3.connect(db)
    conn.execute("UPDATE db_metadata SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    assert ScryfallSyncService(db, printed_langs=["pt"], max_age_days=0).check().reason == "database schema 1 -> 2"


def test_sync_reports_its_progress(tmp_path, monkeypatch):
    fake_scryfall(monkeypatch, [])
    seen = []
    report = ScryfallSyncService(tmp_path / "cards.db", printed_langs=["pt"],
                                 on_progress=lambda *a: seen.append(a)).sync()
    assert report.updated
    stages = [s for s, _, _ in seen]
    assert stages[0] == "checking" and stages[-1] == "finishing"
    order = [stages[0]] + [s for i, s in enumerate(stages[1:], 1) if s != stages[i - 1]]
    assert order == ["checking", "all_cards", "default_cards", "building", "finishing"]
    downloads = [(d, t) for s, d, t in seen if s == "all_cards"]
    assert downloads[-1][0] == downloads[-1][1] > 0                     # bytes, up to the total
    meta = ScryfallSyncService(tmp_path / "cards.db").local_metadata()
    assert meta["default_cards_lines"] == str(len(DEFAULT_CARDS)) and float(meta["build_seconds"]) >= 0


@pytest.mark.parametrize("stop_at", ["all_cards", "default_cards", "building"])
def test_a_cancelled_sync_leaves_the_local_database_alone(tmp_path, monkeypatch, stop_at):
    fake_scryfall(monkeypatch, [])
    db = tmp_path / "cards.db"
    ScryfallSyncService(db, printed_langs=[]).sync()           # the local database, English only
    before = db.read_bytes()
    stop = threading.Event()

    def progress(stage, done, total):
        if stage == stop_at:
            stop.set()
    service = ScryfallSyncService(db, printed_langs=["pt"], on_progress=progress, should_stop=stop.is_set)
    with pytest.raises(SyncCancelled):
        service.sync()
    assert db.read_bytes() == before and not list(tmp_path.glob("*.tmp"))


# ------------------------------------------------------------------ progress and ETA

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_progress_bar_and_eta():
    clock = Clock()
    p = UpdateProgress(build_estimate_s=100, clock=clock)
    assert p.snapshot()["eta_s"] is None and p.snapshot()["fraction"] == 0.0     # still checking
    p.set_downloads({"all_cards": 400_000_000, "default_cards": 80_000_000})
    clock.t = 2
    p.update("all_cards", 0, 400_000_000)
    clock.t = 12
    p.update("all_cards", 100_000_000, 400_000_000)          # 10 MB/s
    s = p.snapshot()
    # 380 MB left at 10 MB/s + the build (100 s) + finishing (5 s)
    assert s["eta_s"] == 38 + 100 + 5 and s["speed_mb_s"] == 10.0
    assert (s["downloaded_mb"], s["download_mb"], s["stage_fraction"]) == (100.0, 480.0, 0.25)
    assert s["fraction"] == round(12 / (12 + 143), 3)

    clock.t = 60
    p.update("building", 0, 100_000)
    clock.t = 80
    p.update("building", 50_000, 100_000)                    # half built in 20 s
    s = p.snapshot()
    assert s["stage"] == "building" and s["eta_s"] == 20 + 5 and s["stage_fraction"] == 0.5
    clock.t = 81
    p.update("building", 50_100, 100_000)
    before = p.snapshot()["fraction"]
    p.build_estimate_s = 10_000                              # a worse estimate never moves the bar back
    clock.t = 82
    assert p.snapshot()["fraction"] >= before
    clock.t = 100
    p.update("finishing", 0, 0)
    assert p.snapshot()["eta_s"] == 5 and p.snapshot()["fraction"] <= 0.99


# ------------------------------------------------------------------ the manager

class FakeService:
    def __init__(self, vision, on_progress=None, should_stop=None, **_):
        self.vision, self.on_progress, self.should_stop = vision, on_progress, should_stop

    def check(self):
        self.vision.checks += 1
        if self.vision.check_error is not None:
            raise self.vision.check_error
        return self.vision.check_result

    def local_metadata(self):
        return {"bulk_updated_at": BULK.updated_at, "printed_langs": "pt", "schema_version": "2",
                "sets_updated_at": "2026-11-17T18:26:02Z"}

    def local_print_count(self):
        return 110_960


class FakeVision(ScriptedVision):
    """ScriptedVision + the card database side of cardforge.cv.VisionModule."""

    def __init__(self, results=(), check=None, check_error=None, update_error=None, gate=None):
        super().__init__(list(results))
        self.check_result = check or UP_TO_DATE
        self.check_error, self.update_error, self.gate = check_error, update_error, gate
        self.checks, self.updates = 0, []

    def sync_service(self, **kw):
        return FakeService(self, **kw)

    def update_database(self, force=False, service=None, check=None, printed_langs=None):
        self.updates.append((force, check))
        for stage, done, total in [("all_cards", 0, ALL.size), ("all_cards", ALL.size, ALL.size),
                                   ("default_cards", BULK.size, BULK.size), ("building", 500, 1000)]:
            if service.should_stop():
                raise SyncCancelled()
            service.on_progress(stage, done, total)
            if self.gate is not None and stage == "building":
                assert self.gate.wait(10)
        if service.should_stop():
            raise SyncCancelled()
        if self.update_error is not None:
            raise self.update_error
        service.on_progress("finishing", 0, 0)
        return SyncReport(True, prints=10, names=12, bulk_updated_at=BULK.updated_at)


class FakeController:
    def __init__(self, busy=False):
        self.busy, self.held, self.holds = busy, False, 0

    def begin_card_db_update(self):
        if self.busy:
            raise ManualRefused(409, "RUN_ACTIVE", "The sorter is busy (running).")
        self.held, self.holds = True, self.holds + 1

    def end_card_db_update(self):
        self.held = False


class Events:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


def manager(vision=None, controller=None, probe=None, wait_s=1.0):
    led = StatusLed(MemoryLedDriver())
    led.ready()
    events = Events()
    m = CardDbManager(vision or FakeVision(), led, controller or FakeController(), events, wait_s=wait_s,
                      network_probe=probe, poll_s=0.01, publish_interval_s=0.0)
    return m, led, events


def done(m):
    if m._thread is not None:
        m._thread.join(10)
    return m.status()


def test_check_tells_the_app_an_update_is_available_without_touching_the_led():
    m, led, events = manager(FakeVision(check=NEW_CARDS))
    assert m.status()["update_available"] is None and m.status()["local"]["cards"] == 110_960
    assert m.start_check()
    s = done(m)
    assert s["update_available"] is True and s["phase"] == "idle"
    assert s["last_check"]["result"] == "update_available"
    assert s["last_check"]["reason"] == "new cards or sets on Scryfall" and s["last_check"]["download_mb"] == 489
    assert events.events[-1] == {"type": "card_db", "card_db": s}
    assert [e["card_db"]["phase"] for e in events.events] == ["checking", "idle"]
    assert led.history == ["STARTING", "IDLE"]                       # a check never blinks the LED


@pytest.mark.parametrize("error, result", [(requests.ConnectionError("Name or service not known"), "unreachable"),
                                           (requests.Timeout("connect timeout"), "unreachable"),
                                           (RuntimeError("bug"), "failed")])
def test_a_check_without_scryfall_keeps_the_local_database(error, result):
    m, _, _ = manager(FakeVision(check_error=error))
    m.start_check()
    s = done(m)
    assert s["last_check"]["result"] == result and s["update_available"] is None


def test_power_on_check_waits_for_the_wifi_only_when_one_is_configured():
    m, _, _ = manager(probe=lambda: NET_SETUP, wait_s=30)
    started = time.monotonic()
    m.start_check(wait_for_network=True)
    assert done(m)["last_check"]["result"] == "offline" and time.monotonic() - started < 1
    assert m.vision.checks == 0
    states = iter([NET_DOWN, NET_DOWN, NET_OK])
    m, _, _ = manager(probe=lambda: next(states), wait_s=5)
    m.start_check(wait_for_network=True)
    assert done(m)["last_check"]["result"] == "up_to_date" and m.vision.checks == 1


def test_update_on_request_with_progress():
    vision = FakeVision(check=NEW_CARDS)
    controller = FakeController()
    m, led, events = manager(vision, controller)
    m.start_update()
    s = done(m)
    assert controller.holds == 1
    assert s["last_update"]["result"] == "updated" and s["update_available"] is False
    assert s["phase"] == "idle" and s["progress"] is None and not controller.held
    assert led.history == ["STARTING", "IDLE", "UPDATING", "IDLE"]
    stages = [e["card_db"]["progress"]["stage"] for e in events.events if e["card_db"]["progress"]]
    assert stages[0] == "checking" and stages[-1] == "finishing" and "building" in stages
    progress = [e["card_db"]["progress"] for e in events.events if e["card_db"]["progress"]]
    assert progress[-1]["download_mb"] == 489.0                      # sizes from the update's own check


def test_update_refused_while_a_run_is_active_or_a_check_runs():
    m, led, _ = manager(controller=FakeController(busy=True))
    with pytest.raises(ManualRefused) as refused:
        m.start_update()
    assert refused.value.code == "RUN_ACTIVE" and m.phase == "idle" and led.mode == "IDLE"
    gate = threading.Event()
    vision = FakeVision()
    vision.check_error = None
    m, _, _ = manager(vision)
    original = vision.sync_service
    vision.sync_service = lambda **kw: (gate.wait(10), original(**kw))[1]
    m.start_check()
    with pytest.raises(CardDbBusy) as busy:
        m.start_update()
    assert busy.value.code == "CARD_DB_CHECKING"
    gate.set()
    done(m)


def test_cancel_keeps_the_local_database():
    gate = threading.Event()
    vision = FakeVision(check=NEW_CARDS, gate=gate)
    m, led, _ = manager(vision)
    m.start_update()
    deadline = time.monotonic() + 5
    while (m.status()["progress"] or {}).get("stage") != "building" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert m.cancel_update()
    gate.set()
    s = done(m)
    assert s["last_update"]["result"] == "cancelled" and s["update_available"] is True   # still to do
    assert led.mode == "IDLE" and not m.controller.held
    assert not m.cancel_update()                                     # nothing left to cancel


def test_update_when_already_up_to_date_downloads_nothing_unless_forced():
    vision = FakeVision(check=UP_TO_DATE)
    m, _, _ = manager(vision)
    m.start_update()
    assert done(m)["last_update"]["result"] == "up_to_date" and vision.updates == []
    m.start_update(force=True)
    assert done(m)["last_update"]["result"] == "updated" and vision.updates[0][0] is True


@pytest.mark.parametrize("error, result", [(requests.ConnectionError("connection reset"), "unreachable"),
                                           (ValueError("broken JSON"), "failed")])
def test_a_failed_update_keeps_the_local_database(error, result):
    m, led, _ = manager(FakeVision(check=NEW_CARDS, update_error=error))
    m.start_update()
    s = done(m)
    assert s["last_update"]["result"] == result and s["update_available"] is True
    assert led.mode == "IDLE" and not m.controller.held


# ------------------------------------------------------------------ in the service

def wait_for(c, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = c.get("/card-db").json()
        if predicate(status):
            return status
        time.sleep(0.01)
    raise AssertionError(c.get("/card-db").json())


def test_power_on_only_checks_and_the_machine_is_ready_at_once():
    vision = FakeVision([identified(card("Plains", "W"))] * 3, check=NEW_CARDS)
    with TestClient(create_app(fast_settings(card_db_check="on"), vision=vision)) as c:
        status = wait_for(c, lambda s: s["update_available"] is not None)
        assert status["update_available"] is True and vision.updates == []
        led = c.app.state.status_led
        assert led.history == ["STARTING", "IDLE"]                   # no blinking at power-on
        assert c.get("/run/status").json()["card_db"]["update_available"] is True
        assert c.post("/run/start").status_code == 200                # an available update blocks nothing
        assert wait_state(c, "idle")["run"]["status"] == "completed"


def test_the_user_starts_follows_and_finishes_an_update():
    gate = threading.Event()
    vision = FakeVision([identified(card("Plains", "W"))] * 3, check=NEW_CARDS, gate=gate)
    with TestClient(create_app(fast_settings(), vision=vision)) as c:
        with c.websocket_connect("/ws/events") as ws:
            assert ws.receive_json()["card_db"]["phase"] == "idle"   # the first snapshot has it
            r = c.post("/card-db/update")
            assert r.status_code == 202 and r.json()["card_db"]["phase"] == "updating"
            assert c.app.state.status_led.mode == "UPDATING"
            refused = c.post("/run/start")
            assert refused.status_code == 409 and refused.json()["problems"][0]["code"] == "CARD_DB_UPDATING"
            assert c.post("/card-db/update").json()["code"] == "CARD_DB_UPDATING"
            event = ws.receive_json()
            while (event["card_db"]["progress"] or {}).get("stage") != "building":
                event = ws.receive_json()
            progress = event["card_db"]["progress"]
            assert event["type"] == "card_db" and 0 <= progress["fraction"] < 1 and progress["eta_s"] is not None
            gate.set()
            while event["card_db"]["phase"] != "idle":
                event = ws.receive_json()
        assert event["card_db"]["last_update"]["result"] == "updated"
        assert event["card_db"]["update_available"] is False
        assert c.app.state.status_led.history[-2:] == ["UPDATING", "IDLE"]
        assert c.post("/run/start").status_code == 200


def test_update_refused_during_a_run_and_cancel_from_the_app():
    gate = threading.Event()
    vision = FakeVision([LOW] * 9, check=NEW_CARDS, gate=gate)
    with TestClient(create_app(fast_settings(sim_stack_cards=10), vision=vision)) as c:
        c.post("/run/start")
        r = c.post("/card-db/update")
        assert r.status_code == 409 and r.json()["code"] == "RUN_ACTIVE"
        c.post("/run/stop")
        wait_state(c, "idle")
        assert c.post("/card-db/update/cancel").json()["code"] == "NOT_UPDATING"
        assert c.post("/card-db/update").status_code == 202
        wait_for(c, lambda s: (s["progress"] or {}).get("stage") == "building")
        assert c.post("/card-db/update/cancel").json()["cancelling"] is True
        gate.set()
        status = wait_for(c, lambda s: s["phase"] == "idle")
        assert status["last_update"]["result"] == "cancelled"
        assert c.app.state.status_led.mode == "IDLE"


def test_manual_check_from_the_app():
    vision = FakeVision(check=UP_TO_DATE)
    with TestClient(create_app(fast_settings(), vision=vision)) as c:
        assert c.get("/card-db").json()["last_check"] is None        # the simulator does not check at power-on
        r = c.post("/card-db/check")
        assert r.status_code in (200, 202)
        status = wait_for(c, lambda s: s["last_check"] is not None)
        assert status["last_check"]["result"] == "up_to_date" and status["update_available"] is False
        assert c.get("/health").json()["card_db"]["update_available"] is False


def test_no_card_database_without_vision():
    with TestClient(create_app(fast_settings())) as c:               # MTG_VISION=off: placeholder
        assert c.get("/card-db").json()["code"] == "CARD_DB_UNAVAILABLE"
        assert c.post("/card-db/update").status_code == 409
        assert c.get("/run/status").json()["card_db"] is None


# ------------------------------------------------------------------ VisionModule

def test_download_does_not_hold_the_vision_lock(monkeypatch, tmp_path):
    from cardforge.cv.vision import VisionModule

    opened = []
    monkeypatch.setattr(VisionModule, "_open_database", lambda self: opened.append(True))
    vision = VisionModule(VisionConfig(db_path=str(tmp_path / "cards.db")), camera=object())

    class Service:
        def sync(self, force=False, before_replace=None, check=None):
            free = []                          # the camera / identification can run during the download

            def use_vision():
                if vision._lock.acquire(timeout=1):
                    free.append(True)
                    vision._lock.release()
            t = threading.Thread(target=use_vision)
            t.start()
            t.join()
            assert free == [True]
            before_replace()                   # the swap itself holds it
            return SyncReport(True)

    assert vision.update_database(service=Service()).updated and opened == [True]
    assert vision._lock.acquire(blocking=False)
    vision._lock.release()


def test_camera_is_opened_even_without_a_database(monkeypatch, tmp_path):
    import cardforge.cv.vision as vision_mod

    class Camera:
        sensor, configured = None, False

        def configure(self):
            self.configured = True

    monkeypatch.setattr(vision_mod, "OcrEngine", lambda *a, **k: object())
    camera = Camera()
    vision = vision_mod.VisionModule(VisionConfig(db_path=str(tmp_path / "missing.db")), camera=camera)
    with pytest.raises(DatabaseUnavailable):
        vision.start(open_camera=True)
    assert camera.configured and not vision.ready
