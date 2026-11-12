"""RF38 on the Pi: faults and run ends go to the server (AlertForwarder), for the user who
started the run, so the app can notify them with the app closed. The last test runs the
whole path on a real PostgreSQL (skipped without CF_TEST_DATABASE_URL)."""

import time

import pytest
from fastapi.testclient import TestClient

from sorter_api.api import create_app
from sorter_api.events import EventHub
from sorter_api.store import RunStore
from sorter_api.sync import AlertForwarder, ServerError, ServerUnreachable
from test_integration import fast_settings, wait_state
from test_sync import OWNER, FakeServer, wait_for

RUN = {"run_id": "run-1", "status": "paused", "cards_processed": 4, "error_code": "FEED_TIMEOUT",
       "fault_at": "2026-11-11T20:03:07.000Z", "stopped_at": None, "message": "Jam. Fix it, then press Resume."}
FAULT = {"type": "fault", "fault": "FEED_TIMEOUT", "time": "2026-11-11T20:03:07.000Z",
         "message": "Jam. Fix it, then press Resume.", "run": RUN}
FINISHED = {"type": "run_finished", "message": "Stack is empty: 12 card(s) processed.",
            "run": {**RUN, "status": "completed", "cards_processed": 12, "error_code": None,
                    "stopped_at": "2026-11-11T20:10:00.000Z"}}


class AlertServer(FakeServer):
    """FakeServer that also takes alerts; fails the first `fail` posts."""

    def __init__(self, fail=0, error=None, **kwargs):
        super().__init__(**kwargs)
        self.alerts = []
        self.fail = fail
        self.error = error

    def post_event(self, alert):
        if self.error is not None:
            raise self.error
        if not self.reachable or self.fail:
            self.fail = max(0, self.fail - 1)
            raise ServerUnreachable("no route to host")
        self.alerts.append(alert)
        return {"id": len(self.alerts), "created": True}


def store_with_run(owner=OWNER):
    store = RunStore(":memory:")
    store.create_run("run-1", "2026-11-11T20:00:00.000Z", owner)
    return store


def test_a_fault_becomes_an_alert_for_the_runs_owner():
    forwarder = AlertForwarder(store_with_run(), AlertServer())
    alert = forwarder.alert_for(FAULT)
    assert alert["owner_id"] == OWNER.user_id and alert["run_id"] == "run-1"
    assert (alert["kind"], alert["code"], alert["time"]) == ("fault", "FEED_TIMEOUT", "2026-11-11T20:03:07.000Z")
    assert alert["cards_processed"] == 4 and alert["status"] is None and alert["event_id"]


def test_the_end_of_a_run_becomes_an_alert_with_its_status():
    alert = AlertForwarder(store_with_run(), AlertServer()).alert_for(FINISHED)
    assert (alert["kind"], alert["status"], alert["code"]) == ("run_ended", "completed", None)
    assert alert["time"] == "2026-11-11T20:10:00.000Z" and alert["cards_processed"] == 12


def test_other_events_and_runs_without_an_owner_send_nothing():
    forwarder = AlertForwarder(store_with_run(), AlertServer())
    assert forwarder.alert_for({"type": "card_sorted", "run_id": "run-1", "card_number": 1}) is None
    assert forwarder.alert_for({"type": "run_started", "run": RUN}) is None
    assert AlertForwarder(store_with_run(owner=None), AlertServer()).alert_for(FAULT) is None
    assert forwarder.alert_for({**FAULT, "run": {**RUN, "run_id": "unknown"}}) is None


def test_an_alert_is_resent_until_the_server_answers():
    server = AlertServer(fail=2)
    forwarder = AlertForwarder(store_with_run(), server, retry_delays=(0.01,))
    forwarder.handle(FAULT)
    assert forwarder.process_pending() == 1
    assert len(server.alerts) == 1 and forwarder.sent == 1 and forwarder.dropped == 0


def test_a_refused_alert_is_not_resent():
    server = AlertServer(error=ServerError("User 7 does not exist", status=422, code="UNKNOWN_USER"))
    forwarder = AlertForwarder(store_with_run(), server, retry_delays=(0.01,))
    forwarder.handle(FAULT)
    assert forwarder.process_pending() == 0 and forwarder.dropped == 1


def test_an_alert_older_than_the_limit_is_dropped():
    now = [1000.0]
    server = AlertServer(reachable=False)
    forwarder = AlertForwarder(store_with_run(), server, max_age_s=60, retry_delays=(30.0,), clock=lambda: now[0])
    forwarder.handle(FAULT)
    now[0] += 45                       # the next retry would be 75 s after the fault
    assert forwarder.process_pending() == 0 and forwarder.dropped == 1


def test_event_listeners_get_every_event_and_cannot_break_publishing():
    hub = EventHub()
    seen = []

    def broken(event):
        raise RuntimeError("bug in a listener")

    hub.add_listener(broken)
    hub.add_listener(seen.append)
    hub.publish(FAULT)                 # no event loop bound: listeners still run
    assert seen == [FAULT]


def test_the_pi_sends_the_jam_and_the_end_of_the_run_to_the_server():
    server = AlertServer()
    with TestClient(create_app(fast_settings(sim_stack_cards=2), server_client=server)) as c:
        c.put("/sim/state", json={"top_jammed": True})
        assert c.post("/run/start", headers={"Authorization": "Bearer good-token"}).status_code == 200
        wait_state(c, "paused")
        wait_for(lambda: len(server.alerts) == 1)
        jam = server.alerts[0]
        assert (jam["kind"], jam["code"], jam["owner_id"]) == ("fault", "FEED_TIMEOUT", OWNER.user_id)
        c.put("/sim/state", json={"top_jammed": False})
        assert c.post("/run/stop").status_code == 200
        wait_state(c, "idle")
        wait_for(lambda: len(server.alerts) == 2)
        end = server.alerts[1]
        assert (end["kind"], end["status"]) == ("run_ended", "cancelled")
        assert end["run_id"] == jam["run_id"]


def test_without_server_alerts_nothing_is_sent():
    server = AlertServer()
    with TestClient(create_app(fast_settings(sim_stack_cards=1, server_alerts=False), server_client=server)) as c:
        assert c.post("/run/start", headers={"Authorization": "Bearer good-token"}).status_code == 200
        wait_state(c, "idle")
        wait_for(lambda: len(server.uploads) == 1)
        time.sleep(0.2)
    assert server.alerts == []


def test_end_to_end_a_jam_reaches_the_apps_notification_feed(server):
    """Real server + PostgreSQL: the app signs up, starts a run, the feeder jams -> the Pi
    sends the fault -> the app's waiting request returns it with its type and time."""
    pytest.importorskip("psycopg")
    from conftest import MACHINE_TOKEN
    from sorter_api.sync import ServerClient

    signup = server.post("/auth/signup", json={"username": "luca", "email": "luca@example.com",
                                               "password": "correct-horse"})
    app_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    start = server.get("/notifications", headers=app_headers).json()["last_id"]

    link = ServerClient("http://testserver", MACHINE_TOKEN, http=server)
    with TestClient(create_app(fast_settings(sim_stack_cards=2), server_client=link)) as pi:
        pi.put("/sim/state", json={"top_jammed": True})
        assert pi.post("/run/start", headers=app_headers).status_code == 200
        page = server.get("/notifications", params={"after": start, "wait": 10}, headers=app_headers).json()
        [jam] = page["notifications"]
        assert (jam["kind"], jam["code"]) == ("fault", "FEED_TIMEOUT") and jam["time"]
        pi.put("/sim/state", json={"top_jammed": False})
        assert pi.post("/run/stop").status_code == 200
        page = server.get("/notifications", params={"after": page["last_id"], "wait": 10},
                          headers=app_headers).json()
        [end] = page["notifications"]
        assert (end["kind"], end["status"], end["run_id"]) == ("run_ended", "cancelled", jam["run_id"])
