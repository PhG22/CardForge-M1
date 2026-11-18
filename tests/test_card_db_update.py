"""Card database (cards.db) check and update when the service starts.

Up to date -> the machine starts as before. New cards on Scryfall -> updated before the machine is
ready, with the status LED blinking white. No network / Scryfall unreachable / any failure -> the
local cards.db is used. Start is refused (CARD_DB_UPDATING) until the check is over.
"""

import threading
import time

import pytest
import requests
from fastapi.testclient import TestClient

from cardforge.cv.config import VisionConfig
from cardforge.data.repository import DatabaseUnavailable
from cardforge.data.sync import BulkEntry, ScryfallSyncService, SyncCheck, SyncReport, sets_fingerprint
from sorter_api.api import create_app
from sorter_api.card_db_update import CardDbUpdater
from sorter_api.status_led import NET_DOWN, NET_OK, NET_SETUP, MemoryLedDriver, StatusLed
from test_integration import LOW, ScriptedVision, card, fast_settings, identified, wait_state
from test_printed_names import fake_scryfall

BULK = BulkEntry("2026-11-17T10:00:00+00:00", "https://x/default.jsonl.gz", 100)


# ------------------------------------------------------------------ status LED

def test_updating_blinks_white_above_everything():
    driver = MemoryLedDriver()
    led = StatusLed(driver)
    led.set_updating(True)
    assert led.mode == "UPDATING" and (driver.color, driver.blink_hz) == ((1, 1, 1), 1.0)
    led.ready()                                      # even "ready" waits for the update
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
    service.sync(check=first)
    assert service.check().current
    assert service.local_metadata()["sets_fingerprint"] == sets_fingerprint(sets["now"])

    sets["now"] = scryfall_sets(ymid=31)                       # digital-only set: not on the machine
    assert service.check().current
    sets["now"] = scryfall_sets(lci=292)                       # one new card
    check = service.check()
    assert not check.current and check.reason == "new cards or sets on Scryfall"

    fetched.clear()
    assert service.sync(check=check).updated                   # reuses the check: no second index fetch
    assert sum(u.endswith("/bulk-data") for u in fetched) == 1 # (the all_cards entry) and no /sets
    assert service.check().current


def test_languages_schema_and_age_also_count(tmp_path, monkeypatch):
    fake_scryfall(monkeypatch, [], updated_at="2026-11-17T10:00:00+00:00")
    db = tmp_path / "cards.db"
    ScryfallSyncService(db, printed_langs=["pt"]).sync()
    assert "languages" in ScryfallSyncService(db, printed_langs=[]).check().reason

    fake_scryfall(monkeypatch, [], updated_at="2027-01-10T10:00:00+00:00")   # same cards, 54 days later
    assert ScryfallSyncService(db, printed_langs=["pt"]).check().reason == "local cards older than 30 days"
    assert ScryfallSyncService(db, printed_langs=["pt"], max_age_days=0).check().current

    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE db_metadata SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    assert ScryfallSyncService(db, printed_langs=["pt"], max_age_days=0).check().reason == "database schema 1 -> 2"


# ------------------------------------------------------------------ the power-on step

class FakeService:
    def __init__(self, check=None, error=None):
        self._check, self.error, self.checks = check, error, 0

    def check(self):
        self.checks += 1
        if self.error is not None:
            raise self.error
        return self._check

    def local_metadata(self):
        return {"bulk_updated_at": "2026-11-17T10:00:00+00:00", "printed_langs": "pt", "schema_version": "2"}


class FakeVision(ScriptedVision):
    """ScriptedVision + the card database side of cardforge.cv.VisionModule."""

    def __init__(self, results=(), check=None, check_error=None, update_error=None, gate=None):
        super().__init__(list(results))
        self.service = FakeService(check or SyncCheck(True, "up to date", BULK), check_error)
        self.update_error, self.gate = update_error, gate
        self.updates = []
        self.led = None                       # set by the test: what the LED showed during the update

    def sync_service(self, **kw):
        return self.service

    def update_database(self, service=None, check=None):
        self.updates.append(check)
        if self.gate is not None:
            self.gate.wait(10)
        if self.update_error is not None:
            raise self.update_error
        return SyncReport(True, prints=10, names=12, bulk_updated_at=BULK.updated_at)


def updater(vision, probe=None, wait_s=1.0):
    led = StatusLed(MemoryLedDriver())
    return CardDbUpdater(vision, led, wait_s=wait_s, network_probe=probe, poll_s=0.01), led


def test_up_to_date_carries_on_without_blinking():
    u, led = updater(FakeVision())
    assert u.run()["result"] == "up_to_date"
    assert u.vision.updates == [] and "UPDATING" not in led.history
    assert u.status()["phase"] == "done" and u.status()["local"]["printed_langs"] == "pt"


def test_new_cards_are_downloaded_with_the_led_blinking_white():
    seen = []
    vision = FakeVision(check=SyncCheck(False, "new cards or sets on Scryfall", BULK))
    u, led = updater(vision)
    vision.update_database = (lambda orig: lambda **kw: (seen.append(led.mode), orig(**kw))[1])(vision.update_database)
    result = u.run()
    assert result["result"] == "updated" and result["reason"] == "new cards or sets on Scryfall"
    assert seen == ["UPDATING"] and len(vision.updates) == 1 and vision.updates[0].reason.startswith("new cards")
    assert led.history == ["STARTING", "UPDATING", "STARTING"]       # back to white: ready() comes later


@pytest.mark.parametrize("error, expected", [
    (requests.ConnectionError("Name or service not known"), "unreachable"),
    (requests.Timeout("connect timeout"), "unreachable"),
    (RuntimeError("bug"), "failed"),
])
def test_no_internet_or_scryfall_down_uses_the_local_database(error, expected):
    u, led = updater(FakeVision(check_error=error))
    assert u.run()["result"] == expected
    assert u.vision.updates == [] and "UPDATING" not in led.history


@pytest.mark.parametrize("error, expected", [
    (requests.ConnectionError("connection reset"), "unreachable"),     # Wi-Fi dropped mid-download
    (OSError(28, "No space left on device"), "unreachable"),
    (ValueError("broken JSON"), "failed"),
])
def test_a_failed_update_keeps_the_local_database(error, expected):
    vision = FakeVision(check=SyncCheck(False, "new cards or sets on Scryfall", BULK), update_error=error)
    u, led = updater(vision)
    assert u.run()["result"] == expected
    assert led.mode == "STARTING" and led.history == ["STARTING", "UPDATING", "STARTING"]


def test_no_wifi_configured_skips_at_once():
    vision = FakeVision()
    u, _ = updater(vision, probe=lambda: NET_SETUP, wait_s=30)
    started = time.monotonic()
    assert u.run()["result"] == "offline" and time.monotonic() - started < 1
    assert vision.service.checks == 0


def test_waits_for_the_wifi_to_connect_at_boot():
    states = iter([NET_DOWN, NET_DOWN, NET_OK])
    vision = FakeVision()
    u, _ = updater(vision, probe=lambda: next(states), wait_s=5)
    assert u.run()["result"] == "up_to_date" and vision.service.checks == 1


def test_gives_up_waiting_for_the_wifi():
    vision = FakeVision()
    u, _ = updater(vision, probe=lambda: NET_DOWN, wait_s=0.05)
    assert u.run()["result"] == "offline" and vision.service.checks == 0


# ------------------------------------------------------------------ in the service

def test_update_at_power_on_holds_the_machine_until_done():
    gate = threading.Event()
    vision = FakeVision([identified(card("Plains", "W"))] * 3,
                        check=SyncCheck(False, "new cards or sets on Scryfall", BULK), gate=gate)
    with TestClient(create_app(fast_settings(card_db_update="on"), vision=vision)) as c:
        led = c.app.state.status_led
        deadline = time.monotonic() + 5
        while led.mode != "UPDATING" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert led.mode == "UPDATING"
        status = c.get("/run/status").json()
        assert status["startup"] == "card_db" and status["card_db"]["phase"] == "updating"
        r = c.post("/run/start")
        assert r.status_code == 409 and r.json()["problems"][0]["code"] == "CARD_DB_UPDATING"

        gate.set()                                                # the download ends
        deadline = time.monotonic() + 5
        while c.get("/run/status").json()["startup"] is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert led.history == ["STARTING", "UPDATING", "STARTING", "IDLE"]
        assert c.get("/health").json()["card_db"]["result"] == "updated"
        assert c.post("/run/start").status_code == 200
        assert wait_state(c, "idle")["run"]["status"] == "completed"


def test_offline_start_is_the_same_as_before():
    vision = FakeVision([LOW] * 9, check_error=requests.ConnectionError("no route to host"))
    with TestClient(create_app(fast_settings(card_db_update="on"), vision=vision)) as c:
        deadline = time.monotonic() + 5
        while c.get("/run/status").json()["startup"] is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        led = c.app.state.status_led
        assert led.history == ["STARTING", "IDLE"]
        assert c.get("/run/status").json()["card_db"]["result"] == "unreachable"
        assert c.post("/run/start").status_code == 200


def test_off_by_default_with_the_simulator():
    with TestClient(create_app(fast_settings(), vision=FakeVision())) as c:
        assert c.app.state.card_db_updater is None
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
