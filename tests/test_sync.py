"""The Pi side of the database: run owner at Start, run.db migration, the SyncWorker,
and one end-to-end run (simulated machine -> run.db -> upload -> PostgreSQL -> app queries)."""

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from sorter_api.api import create_app
from sorter_api.store import Owner, RunStore, ScanRecord
from sorter_api.sync import NotSignedIn, ServerClient, ServerError, ServerUnreachable, SyncWorker
from test_integration import ScriptedVision, card, fast_settings, identified, wait_state

OWNER = Owner(user_id=7, username="luca")


class FakeServer:
    """Stands in for ServerClient: one valid user token, records the uploads."""

    base_url = "http://server.test"

    def __init__(self, reachable=True, fail_uploads=0):
        self.reachable = reachable
        self.fail_uploads = fail_uploads
        self.uploads = []

    def whoami(self, token):
        if not self.reachable:
            raise ServerUnreachable("no route to host")
        if token != "good-token":
            raise NotSignedIn("Sign in to the app before starting a run.")
        return OWNER

    def upload_run(self, payload):
        if not self.reachable or self.fail_uploads:
            self.fail_uploads = max(0, self.fail_uploads - 1)
            raise ServerUnreachable("no route to host")
        self.uploads.append(payload)
        return {"run_id": len(self.uploads), "cards_received": len(payload["cards"])}

    def close(self):
        pass


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


# ------------------------------------------------------------------ run.db

OLD_SCHEMA = """
CREATE TABLE local_run (local_uuid TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL,
    stopped_at TEXT, cards_processed INTEGER NOT NULL DEFAULT 0, successful_scans INTEGER NOT NULL DEFAULT 0,
    failed_scans INTEGER NOT NULL DEFAULT 0, error_code TEXT, message TEXT,
    sync_state TEXT NOT NULL DEFAULT 'pending', sync_attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE local_cards (local_uuid TEXT PRIMARY KEY, run_uuid TEXT NOT NULL REFERENCES local_run(local_uuid),
    card_number INTEGER NOT NULL, card_identifier TEXT, scan_status TEXT NOT NULL, bin INTEGER,
    match_method TEXT, confidence INTEGER, attempts INTEGER, card_name TEXT, set_code TEXT,
    collector_number TEXT, color_identity TEXT, type_line TEXT, mana_cost TEXT, rarity TEXT,
    scanned_at TEXT NOT NULL, error_message TEXT, UNIQUE (run_uuid, card_number));
INSERT INTO local_run (local_uuid, status, started_at, stopped_at, cards_processed, failed_scans)
    VALUES ('old-run', 'completed', '2026-11-01T10:00:00.000Z', '2026-11-01T10:05:00.000Z', 1, 1);
INSERT INTO local_cards (local_uuid, run_uuid, card_number, scan_status, bin, scanned_at)
    VALUES ('old-card', 'old-run', 1, 'unidentified', 6, '2026-11-01T10:01:00.000Z');
"""


def test_a_run_db_from_the_previous_version_is_migrated_in_place(tmp_path):
    path = tmp_path / "run.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
    store = RunStore(path)
    assert store.get_run("old-run")["cards_processed"] == 1            # data kept
    assert store.get_run("old-run")["started_by"] is None
    assert store.runs_to_sync("2099-01-01T00:00:00.000Z") == []        # no owner: stays on the Pi
    assert store.sync_status()["local_only"] == 1
    store.create_run("new-run", "2026-11-11T10:00:00.000Z", OWNER)      # the new columns work
    store.close()
    store = RunStore(path)                                                # opening again is a no-op
    assert store.get_run("new-run")["owner_name"] == "luca"
    store.close()


def test_cards_belong_to_the_runs_owner_and_go_in_the_upload():
    store = RunStore(":memory:")
    store.create_run("r1", "2026-11-11T10:00:00.000Z", OWNER)
    store.save_scan("r1", 1, "2026-11-11T10:00:05.000Z", ScanRecord(
        scan_status="identified", bin=1, card_identifier="print-1", card_name="Plains", set_code="FIN",
        collector_number="1", color_identity="W", confidence=100, attempts=1))
    store.save_scan("r1", 2, "2026-11-11T10:00:09.000Z", ScanRecord(scan_status="face_down", bin=6, attempts=1))
    assert store.sync_payload("r1") is None                              # still running: not uploaded
    assert store.runs_to_sync("2099-01-01T00:00:00.000Z") == []
    store.update_run("r1", status="completed", stopped_at="2026-11-11T10:01:00.000Z", message="done")

    assert store.runs_to_sync("2099-01-01T00:00:00.000Z") == ["r1"]
    payload = store.sync_payload("r1")
    assert payload["run"]["started_by"] == 7
    assert (payload["run"]["cards_processed"], payload["run"]["successful_scans"],
            payload["run"]["failed_scans"]) == (2, 1, 1)
    assert [c["owner_id"] for c in payload["cards"]] == [7, 7]
    assert payload["cards"][0]["card_name"] == "Plains" and payload["cards"][1]["scan_status"] == "face_down"


def test_worker_retries_with_backoff_then_marks_the_run_synced():
    now = [datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)]
    store = RunStore(":memory:")
    store.create_run("r1", "2026-11-11T10:00:00.000Z", OWNER)
    store.update_run("r1", status="cancelled", stopped_at="2026-11-11T10:01:00.000Z")
    server = FakeServer(fail_uploads=2)
    worker = SyncWorker(store, server, clock=lambda: now[0])

    assert worker.sync_pending() == 0                                    # 1st failure: retry in 10 s
    run = store.get_run("r1")
    assert (run["sync_state"], run["sync_attempts"]) == ("failed", 1)
    assert run["next_sync_at"] == "2026-11-11T12:00:10.000Z" and "no route" in run["last_sync_error"]
    assert worker.sync_pending() == 0 and server.uploads == []          # not due yet: not even tried
    now[0] += timedelta(seconds=10)
    assert worker.sync_pending() == 0                                    # 2nd failure: retry in 20 s
    assert store.get_run("r1")["next_sync_at"] == "2026-11-11T12:00:30.000Z"
    now[0] += timedelta(seconds=20)
    assert worker.sync_pending() == 1
    run = store.get_run("r1")
    assert (run["sync_state"], run["remote_run_id"], run["last_sync_error"]) == ("synced", 1, None)
    assert worker.sync_pending() == 0 and len(server.uploads) == 1       # never uploaded twice
    assert store.sync_status() == {"synced": 1, "pending": 0, "failed": 0, "local_only": 0, "last_error": None}


def test_backoff_is_capped():
    worker = SyncWorker(RunStore(":memory:"), FakeServer(), retry_max_s=300)
    assert [worker.retry_delay(n) for n in (1, 2, 3, 4, 5, 6)] == [10, 20, 40, 80, 160, 300]


def test_server_client_reports_an_unreachable_server():
    client = ServerClient("http://127.0.0.1:9", "token", timeout_s=0.5)
    with pytest.raises(ServerUnreachable):
        client.upload_run({"run": {}, "cards": []})
    with pytest.raises(NotSignedIn):
        client.whoami(None)
    client.close()


# ------------------------------------------------------------------ Start needs a signed-in user

def test_with_a_server_start_needs_the_app_users_token():
    server = FakeServer()
    with TestClient(create_app(fast_settings(), server_client=server)) as c:
        r = c.post("/run/start")
        assert r.status_code == 401
        assert r.json()["problems"][0]["code"] == "SIGN_IN_REQUIRED"
        r = c.post("/run/start", headers={"Authorization": "Bearer expired"})
        assert r.status_code == 401
        server.reachable = False
        r = c.post("/run/start", headers={"Authorization": "Bearer good-token"})
        assert r.status_code == 503 and r.json()["problems"][0]["code"] == "SERVER_UNREACHABLE"
        assert c.get("/run/status").json()["state"] == "idle"            # nothing was started
        assert c.get("/auth/login").status_code in (404, 405)           # the Pi no longer stands in for the server


def test_finished_runs_are_uploaded_as_soon_as_they_end():
    server = FakeServer()
    with TestClient(create_app(fast_settings(sim_stack_cards=2), server_client=server)) as c:
        r = c.post("/run/start", headers={"Authorization": "Bearer good-token"})
        assert r.status_code == 200, r.text
        assert r.json()["run"]["owner"] == "luca"
        wait_state(c, "idle")
        wait_for(lambda: len(server.uploads) == 1)
        payload = server.uploads[0]
        assert payload["run"]["started_by"] == 7 and payload["run"]["status"] == "completed"
        assert len(payload["cards"]) == 2 and {card["owner_id"] for card in payload["cards"]} == {7}
        status = c.get("/sync/status").json()
        assert status["enabled"] is True and status["synced"] == 1


def test_runs_wait_on_the_pi_while_the_server_is_down():
    server = FakeServer()
    with TestClient(create_app(fast_settings(sim_stack_cards=1), server_client=server)) as c:
        assert c.post("/run/start", headers={"Authorization": "Bearer good-token"}).status_code == 200
        server.reachable = False                                        # Wi-Fi drops during the run
        wait_state(c, "idle")
        wait_for(lambda: c.get("/sync/status").json()["failed"] == 1)
        assert c.get("/sync/status").json()["last_error"]["sync_attempts"] >= 1
        server.reachable = True                                         # back online
        assert c.post("/sync/now").status_code == 200
        wait_for(lambda: c.get("/sync/status").json()["synced"] == 1)
        assert len(server.uploads) == 1


# ------------------------------------------------------------------ end to end with PostgreSQL

def test_end_to_end_run_is_saved_to_the_users_account(server):
    """App signs up on the server -> starts a run on the Pi with its token -> the Pi saves each
    card in run.db -> at the end the SyncWorker uploads the run -> the app reads its runs,
    its collection and the export rows from the server."""
    signup = server.post("/auth/signup", json={"username": "luca", "email": "luca@example.com",
                                               "password": "correct-horse"})
    token = signup.json()["access_token"]
    app_headers = {"Authorization": f"Bearer {token}"}
    from conftest import MACHINE_TOKEN

    link = ServerClient("http://testserver", MACHINE_TOKEN, http=server)
    vision = ScriptedVision([identified(card("Plains", "W")), identified(card("Lightning Bolt", "R")),
                             identified(card("Lightning Bolt", "R"))])
    with TestClient(create_app(fast_settings(sim_stack_cards=3), vision=vision, server_client=link)) as pi:
        assert pi.post("/run/start", headers=app_headers).status_code == 200
        wait_state(pi, "idle")
        wait_for(lambda: pi.get("/sync/status").json()["synced"] == 1)

    runs = server.get("/runs", headers=app_headers).json()
    assert runs["total"] == 1 and runs["runs"][0]["cards_processed"] == 3
    results = server.get(f"/runs/{runs['runs'][0]['run_id']}", headers=app_headers).json()
    assert results["bin_counts"] == {"1": 1, "4": 2}
    collection = server.get("/cards", headers=app_headers).json()
    assert [(i["name"], i["folders"]) for i in collection["items"]] == [
        ("Lightning Bolt", [{"folder": 4, "quantity": 2}]), ("Plains", [{"folder": 1, "quantity": 1}])]
    export = server.get("/cards/export", headers=app_headers).json()
    assert export["total_cards"] == 3


def test_server_errors_are_not_mistaken_for_success():
    class Rejecting(FakeServer):
        def upload_run(self, payload):
            raise ServerError("The run counters do not match", status=422, code="INCONSISTENT_RUN")

    store = RunStore(":memory:")
    store.create_run("r1", "2026-11-11T10:00:00.000Z", OWNER)
    store.update_run("r1", status="completed", stopped_at="2026-11-11T10:01:00.000Z")
    SyncWorker(store, Rejecting()).sync_pending()
    assert store.get_run("r1")["sync_state"] == "failed"
    assert store.sync_status()["last_error"]["last_sync_error"] == "The run counters do not match"
