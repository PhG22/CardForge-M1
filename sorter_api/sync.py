"""The Pi's link to the CardForge server (cardforge_server/, PostgreSQL behind it).

* ServerClient.whoami(): at Start, checks the app user's access token with the server
  (GET /auth/me) and returns the Owner the run and its cards will belong to.
* SyncWorker (DB02 / DB04): uploads every finished run in run.db, with all its cards,
  to POST /sync/runs. It wakes when a run ends (notify()) and every sync_interval_s.
  The server stores a run and its cards in one transaction and matches them by
  local_uuid, so a retried upload never duplicates anything. A run is marked synced
  only after the server confirmed it; a failed upload is retried with exponential
  backoff (10 s, 20 s, 40 s ... up to sync_retry_max_s), also after a reboot.

* AlertForwarder (RF38): sends each alert of a run (a fault that paused it, the end of
  the run) to POST /machine/events for the user who started the run. The server keeps
  it, and the app's background service shows it as a notification even when the app is
  closed or the phone is locked. Resent with backoff while the server is unreachable,
  for at most alert_max_age_s; the server stores a resent alert once (event_id).

Scans never wait for the network: each card is committed to run.db first (RNF19/RNF20).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

import httpx

from .store import Owner, RunStore

log = logging.getLogger(__name__)

FIRST_RETRY_S = 10.0


class ServerError(Exception):
    """The server could not be used for this request."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class ServerUnreachable(ServerError):
    """No answer (network down, server off, timeout)."""


class NotSignedIn(ServerError):
    """The app user's token is missing, expired or unknown to the server."""


class ServerClient:
    def __init__(self, base_url: str, machine_token: str, timeout_s: float = 5.0,
                 http: httpx.Client | None = None) -> None:
        """http: a ready client (tests pass the server's TestClient); else one is made for base_url."""
        self.base_url = base_url.rstrip("/")
        self.machine_token = machine_token
        self._http = http or httpx.Client(base_url=self.base_url, timeout=timeout_s)

    def close(self) -> None:
        self._http.close()

    def whoami(self, user_token: str | None) -> Owner:
        if not user_token:
            raise NotSignedIn("Sign in to the app before starting a run.")
        response = self._call("GET", "/auth/me", user_token)
        if response.status_code == 401:
            raise NotSignedIn("Your session expired. Sign in to the app again.", status=401)
        body = self._json(response)
        try:
            return Owner(user_id=int(body["id"]), username=str(body["username"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ServerError(f"Unexpected answer from the server: {body!r}") from exc

    def upload_run(self, payload: dict) -> dict:
        """POST /sync/runs. Returns {run_id, created, cards_received, cards_inserted}."""
        return self._json(self._call("POST", "/sync/runs", self.machine_token, json=payload))

    def post_event(self, alert: dict) -> dict:
        """POST /machine/events (RF38). Returns {id, created}."""
        return self._json(self._call("POST", "/machine/events", self.machine_token, json=alert))

    def _call(self, method: str, path: str, token: str, json: dict | None = None) -> httpx.Response:
        try:
            return self._http.request(method, path, json=json, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise ServerUnreachable(f"Cannot reach the CardForge server at {self.base_url}: {exc}") from exc

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or not isinstance(body, dict):
            message = body.get("message") if isinstance(body, dict) else None
            raise ServerError(message or f"HTTP {response.status_code}", status=response.status_code,
                              code=body.get("code") if isinstance(body, dict) else None)
        return body


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SyncWorker:
    def __init__(self, store: RunStore, client: ServerClient, interval_s: float = 60.0,
                 retry_max_s: float = 900.0, clock=lambda: datetime.now(timezone.utc)) -> None:
        self.store = store
        self.client = client
        self.interval_s = interval_s
        self.retry_max_s = retry_max_s
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._pass_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="sync-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def notify(self) -> None:
        """A run just finished: upload it now instead of at the next interval."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync_pending()
            except Exception:                       # never let the worker die
                log.exception("sync pass failed")
            self._wake.wait(self.interval_s)
            self._wake.clear()

    def retry_delay(self, attempts: int) -> float:
        """Seconds to wait after the given number of failed uploads: 10, 20, 40 ... up to retry_max_s."""
        return min(self.retry_max_s, FIRST_RETRY_S * 2 ** max(0, attempts - 1))

    def sync_pending(self) -> int:
        """One pass over the queue. Returns how many runs were uploaded."""
        with self._pass_lock:
            done = 0
            for run_id in self.store.runs_to_sync(_iso(self._clock())):
                if self._stop.is_set():
                    break
                done += self._sync_one(run_id)
            return done

    def _sync_one(self, run_id: str) -> int:
        payload = self.store.sync_payload(run_id)
        if payload is None:
            return 0
        try:
            result = self.client.upload_run(payload)
        except ServerError as exc:
            attempts = self.store.mark_sync_failed(
                run_id, str(exc), lambda n: _iso(self._clock() + timedelta(seconds=self.retry_delay(n))))
            level = logging.WARNING if isinstance(exc, ServerUnreachable) else logging.ERROR
            log.log(level, "upload of run %s failed (attempt %d), next try in %.0f s: %s",
                    run_id, attempts, self.retry_delay(attempts), exc)
            return 0
        self.store.mark_synced(run_id, int(result["run_id"]), _iso(self._clock()))
        log.info("run %s uploaded to the server as #%s (%s card(s))", run_id, result["run_id"],
                 result.get("cards_received"))
        return 1


# ------------------------------------------------------------------ alerts (RF38)

ALERT_RETRY_DELAYS_S = (2.0, 5.0, 10.0, 30.0, 60.0)
ALERT_MESSAGE_MAX = 500          # = the server's limit


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


class AlertForwarder:
    """Listens to the controller's events (EventHub.add_listener) and sends the alerts of
    runs that have an owner to the server, one at a time and in order."""

    def __init__(self, store: RunStore, client: ServerClient, max_age_s: float = 900.0,
                 retry_delays: Iterable[float] = ALERT_RETRY_DELAYS_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.client = client
        self.max_age_s = max_age_s
        self.retry_delays = tuple(retry_delays)
        self._clock = clock
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent = 0
        self.dropped = 0

    # -- EventHub listener (runs in the thread that published the event)

    def handle(self, event: dict) -> None:
        alert = self.alert_for(event)
        if alert is None:
            return
        try:
            self._queue.put_nowait((self._clock(), alert))
        except queue.Full:
            self.dropped += 1
            log.warning("too many alerts waiting for the server: %s %s dropped", alert["kind"], alert["code"])

    def alert_for(self, event: dict) -> dict | None:
        """The server alert for a controller event, or None if it is not one (or the run has no owner)."""
        kind = {"fault": "fault", "run_finished": "run_ended"}.get(event.get("type"))
        run = event.get("run") or {}
        run_id = run.get("run_id")
        if kind is None or not run_id:
            return None
        row = self.store.get_run(run_id)
        owner_id = row.get("started_by") if row else None
        if owner_id is None:                  # started without a server: nobody to tell
            return None
        message = event.get("message") or run.get("message")
        alert = {"event_id": str(uuid.uuid4()), "owner_id": int(owner_id), "run_id": run_id, "kind": kind,
                 "message": message[:ALERT_MESSAGE_MAX] if message else None,
                 "cards_processed": run.get("cards_processed")}
        if kind == "fault":
            alert.update(code=event.get("fault") or run.get("error_code") or "FAULT", status=None,
                         time=event.get("time") or run.get("fault_at") or _now_iso())
        else:
            alert.update(code=run.get("error_code"), status=run.get("status"),
                         time=run.get("stopped_at") or _now_iso())
        return alert

    # -- delivery

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="alert-forwarder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def pending(self) -> int:
        return self._queue.qsize()

    def process_pending(self) -> int:
        """Send what is queued now, in the calling thread (tests). Returns how many were sent."""
        sent = 0
        while True:
            try:
                queued_at, alert = self._queue.get_nowait()
            except queue.Empty:
                return sent
            sent += self._deliver(queued_at, alert)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                queued_at, alert = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._deliver(queued_at, alert)
            except Exception:                 # never let the forwarder die
                log.exception("alert delivery failed")

    def _deliver(self, queued_at: float, alert: dict) -> int:
        attempt = 0
        while True:
            try:
                result = self.client.post_event(alert)
            except ServerError as exc:
                retry = isinstance(exc, ServerUnreachable) or exc.status is None or exc.status >= 500
                delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)] if self.retry_delays else 0.0
                if not retry or self._clock() + delay - queued_at > self.max_age_s:
                    self.dropped += 1
                    log.error("alert %s %s for run %s not delivered: %s", alert["kind"],
                              alert["code"] or alert["status"], alert["run_id"], exc)
                    return 0
                attempt += 1
                log.warning("alert %s for run %s: server unreachable, retry %d in %.0f s (%s)",
                            alert["kind"], alert["run_id"], attempt, delay, exc)
                if self._stop.wait(delay):
                    return 0
                continue
            self.sent += 1
            log.info("alert %s %s for run %s sent to the server (#%s)", alert["kind"],
                     alert["code"] or alert["status"], alert["run_id"], result.get("id"))
            return 1
