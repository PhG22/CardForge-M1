"""The Pi's link to the CardForge server (cardforge_server/, PostgreSQL behind it).

* ServerClient.whoami(): at Start, checks the app user's access token with the server
  (GET /auth/me) and returns the Owner the run and its cards will belong to.
* SyncWorker (DB02 / DB04): uploads every finished run in run.db, with all its cards,
  to POST /sync/runs. It wakes when a run ends (notify()) and every sync_interval_s.
  The server stores a run and its cards in one transaction and matches them by
  local_uuid, so a retried upload never duplicates anything. A run is marked synced
  only after the server confirmed it; a failed upload is retried with exponential
  backoff (10 s, 20 s, 40 s ... up to sync_retry_max_s), also after a reboot.

Scans never wait for the network: each card is committed to run.db first (RNF19/RNF20).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

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
