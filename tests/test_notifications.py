"""RF38 on the server: the Pi stores alerts (POST /machine/events), the app's background
service reads them (GET /notifications), waiting for the next one. Real PostgreSQL;
skipped without CF_TEST_DATABASE_URL (see conftest.py)."""

import threading
import time
import uuid

import pytest

from test_server import MACHINE, signup

psycopg = pytest.importorskip("psycopg", reason="server dependencies: pip install -r cardforge_server/requirements.txt")


def fault(owner_id, code="FEED_TIMEOUT", run_id=None, event_id=None, time_="2026-11-11T20:03:07.000Z"):
    return {"event_id": event_id or str(uuid.uuid4()), "owner_id": owner_id, "run_id": run_id or str(uuid.uuid4()),
            "kind": "fault", "code": code, "message": "The card did not reach the platform. Fix it, then press Resume.",
            "cards_processed": 4, "time": time_}


def run_ended(owner_id, status="completed", run_id=None, code=None):
    return {"event_id": str(uuid.uuid4()), "owner_id": owner_id, "run_id": run_id or str(uuid.uuid4()),
            "kind": "run_ended", "status": status, "code": code, "message": "Stack is empty: 12 card(s) processed.",
            "cards_processed": 12, "time": "2026-11-11T20:10:00.000Z"}


def test_an_alert_reaches_only_the_user_who_started_the_run(server):
    luca, user = signup(server, "luca")
    ana, _ = signup(server, "ana")
    start = server.get("/notifications", headers=luca).json()
    assert start == {"notifications": [], "last_id": 0}

    r = server.post("/machine/events", headers=MACHINE, json=fault(user["id"]))
    assert r.status_code == 201, r.text
    assert r.json()["created"] is True

    page = server.get("/notifications", params={"after": start["last_id"]}, headers=luca).json()
    [alert] = page["notifications"]
    assert alert["kind"] == "fault" and alert["code"] == "FEED_TIMEOUT"
    assert alert["time"] == "2026-11-11T20:03:07.000Z"        # RF38: event type and timestamp
    assert alert["cards_processed"] == 4
    assert page["last_id"] == alert["id"]
    assert server.get("/notifications", params={"after": 0}, headers=ana).json()["notifications"] == []


def test_a_resent_alert_is_stored_once(server):
    _, user = signup(server)
    body = fault(user["id"])
    first = server.post("/machine/events", headers=MACHINE, json=body).json()
    again = server.post("/machine/events", headers=MACHINE, json=body).json()
    assert again == {"id": first["id"], "created": False}


def test_a_watcher_starts_after_the_alerts_it_already_missed(server):
    luca, user = signup(server)
    server.post("/machine/events", headers=MACHINE, json=fault(user["id"]))
    server.post("/machine/events", headers=MACHINE, json=run_ended(user["id"]))
    start = server.get("/notifications", headers=luca).json()
    assert start["notifications"] == [] and start["last_id"] == 2
    assert server.get("/notifications", params={"after": start["last_id"]}, headers=luca).json()["notifications"] == []
    everything = server.get("/notifications", params={"after": 0}, headers=luca).json()["notifications"]
    assert [a["kind"] for a in everything] == ["fault", "run_ended"]
    assert everything[1]["status"] == "completed"


def test_a_waiting_request_returns_as_soon_as_an_alert_arrives(server):
    luca, user = signup(server)
    result = {}

    def wait_for_alert():
        began = time.monotonic()
        result["page"] = server.get("/notifications", params={"after": 0, "wait": 20}, headers=luca).json()
        result["took"] = time.monotonic() - began

    waiter = threading.Thread(target=wait_for_alert)
    waiter.start()
    deadline = time.monotonic() + 5
    while server.app.state.notifications.waiting(user["id"]) == 0:      # the request is waiting
        assert time.monotonic() < deadline, "the request never started waiting"
        time.sleep(0.02)
    server.post("/machine/events", headers=MACHINE, json=fault(user["id"], code="DROP_FAILED"))
    waiter.join(10)
    assert not waiter.is_alive()
    assert [a["code"] for a in result["page"]["notifications"]] == ["DROP_FAILED"]
    assert result["took"] < 5


def test_a_wait_without_alerts_ends_empty_after_the_timeout(server):
    luca, _ = signup(server)
    began = time.monotonic()
    page = server.get("/notifications", params={"after": 0, "wait": 0.5}, headers=luca).json()
    assert page == {"notifications": [], "last_id": 0}
    assert 0.4 < time.monotonic() - began < 5


def test_an_after_beyond_the_last_alert_restarts_from_the_last_id(server):
    luca, user = signup(server)
    server.post("/machine/events", headers=MACHINE, json=fault(user["id"]))
    page = server.get("/notifications", params={"after": 999}, headers=luca).json()
    assert page == {"notifications": [], "last_id": 1}


def test_alerts_need_the_right_tokens_and_valid_fields(server):
    luca, user = signup(server)
    assert server.post("/machine/events", json=fault(user["id"])).status_code == 401
    assert server.post("/machine/events", headers={"Authorization": "Bearer nope"},
                       json=fault(user["id"])).status_code == 401
    assert server.get("/notifications").status_code == 401
    assert server.get("/notifications", headers=MACHINE).status_code == 401      # a machine token is no user

    unknown = server.post("/machine/events", headers=MACHINE, json=fault(9999))
    assert unknown.status_code == 422 and unknown.json()["code"] == "UNKNOWN_USER"
    no_code = server.post("/machine/events", headers=MACHINE, json={**fault(user["id"]), "code": None})
    assert no_code.status_code == 422 and no_code.json()["code"] == "INVALID_EVENT"
    no_status = server.post("/machine/events", headers=MACHINE, json={**run_ended(user["id"]), "status": None})
    assert no_status.status_code == 422 and no_status.json()["code"] == "INVALID_EVENT"
    bad_kind = server.post("/machine/events", headers=MACHINE, json={**fault(user["id"]), "kind": "party"})
    assert bad_kind.status_code == 422


def test_a_disabled_account_gets_no_alerts(server, pg_url):
    luca, user = signup(server)
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("UPDATE users SET is_active = false WHERE id = %s", (user["id"],))
    assert server.get("/notifications", params={"after": 0}, headers=luca).status_code == 401


def test_alerts_older_than_30_days_are_deleted(server, pg_url):
    luca, user = signup(server)
    server.post("/machine/events", headers=MACHINE, json=fault(user["id"]))
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("UPDATE notifications SET created_at = now() - interval '31 days'")
    server.post("/machine/events", headers=MACHINE, json=fault(user["id"], code="DROP_FAILED"))
    codes = [a["code"] for a in server.get("/notifications", params={"after": 0}, headers=luca).json()["notifications"]]
    assert codes == ["DROP_FAILED"]



def test_a_watcher_can_follow_one_run_only(server):
    luca, user = signup(server)
    this_run, other_run = str(uuid.uuid4()), str(uuid.uuid4())
    server.post("/machine/events", headers=MACHINE, json=fault(user["id"], run_id=other_run))
    server.post("/machine/events", headers=MACHINE, json=fault(user["id"], code="DROP_FAILED", run_id=this_run))
    server.post("/machine/events", headers=MACHINE, json=run_ended(user["id"], run_id=this_run))
    page = server.get("/notifications", params={"after": 0, "run_id": this_run}, headers=luca).json()
    assert [(a["kind"], a["run_id"]) for a in page["notifications"]] == [("fault", this_run), ("run_ended", this_run)]
    assert page["last_id"] == 3
    assert server.get("/notifications", params={"after": 0, "run_id": "not-a-uuid"},
                      headers=luca).status_code == 422
