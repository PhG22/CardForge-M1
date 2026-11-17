"""Card database update when the service starts (data/cards.db, from Scryfall).

Runs in the controller's power-on sequence, before the base homing and before the status LED
leaves STARTING (white), so the machine never shows "ready" with an update half done:

1. Network. No Wi-Fi configured yet (pair it over Bluetooth first) -> skip at once. Wi-Fi
   configured but not connected yet (the service starts at boot, often before NetworkManager has
   connected) -> wait up to `wait_s` for it. No NetworkManager (a PC) -> just try.
2. Check, 2 small requests (ScryfallSyncService.check): new sets or cards on Scryfall, another
   database schema or printed-name languages, or a database older than db_max_age_days?
   Scryfall rebuilds its bulk files every day (prices, images): that alone is not an update.
3. Update, only if needed: status LED blinking white. The download (~480 MB, streamed) and the
   new cards.db are built while the old one stays in use, and swapped in at the end.

No network, Scryfall unreachable or any failure: the machine carries on with the local
cards.db, which the atomic swap never leaves half written. Start is refused
(409 CARD_DB_UPDATING) until this is over. GET /health and GET /run/status show its state.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .status_led import NET_OK, NET_SETUP

log = logging.getLogger(__name__)

NETWORK_POLL_S = 2.0
LOCAL_KEYS = ("bulk_updated_at", "printed_langs", "schema_version", "localized_names")


def network_error(exc: BaseException) -> bool:
    """No internet / Scryfall down or unreachable, as opposed to a bug or a full disk."""
    try:
        import requests
    except ImportError:                       # pragma: no cover - requests is a requirement
        requests = None
    if requests is not None and isinstance(exc, requests.RequestException):
        response = getattr(exc, "response", None)
        return response is None or response.status_code >= 500 or response.status_code == 429
    return isinstance(exc, (OSError, EOFError, TimeoutError))


class CardDbUpdater:
    def __init__(self, vision, led, wait_s: float = 30.0, network_probe: Optional[Callable[[], str]] = None,
                 service=None, poll_s: float = NETWORK_POLL_S) -> None:
        """vision: the cardforge.cv VisionModule (update_database, sync_service).
        network_probe: status_led.nmcli_network_state, or None when there is no NetworkManager.
        service: a ScryfallSyncService to use instead of vision.sync_service() (tests)."""
        self.vision = vision
        self.led = led
        self.wait_s = wait_s
        self.network_probe = network_probe
        self.poll_s = poll_s
        self._service = service
        self._cancel = threading.Event()
        self.phase = "pending"                # pending | waiting_network | checking | updating | done
        self.progress: Optional[dict] = None  # while updating: {"stage": "all_cards", "fraction": 0.42}
        self.result: Optional[dict] = None    # once done: result, message, reason, seconds, local...

    def status(self) -> dict:
        return {"phase": self.phase, "progress": self.progress, **(self.result or {})}

    def cancel(self) -> None:
        """Service stopping: do not wait for the network any longer."""
        self._cancel.set()

    # ------------------------------------------------------------------ the power-on step

    def run(self) -> dict:
        started = time.monotonic()
        service = None
        try:
            if not self._wait_for_network():
                return self._finish("offline", "no network: using the local card database", started, service)
            service = self._service or self.vision.sync_service(on_progress=self._on_progress)
            self.phase = "checking"
            try:
                check = service.check()
            except Exception as exc:
                return self._finish("unreachable" if network_error(exc) else "failed",
                                    f"could not check Scryfall ({exc}): using the local card database",
                                    started, service)
            if check.current:
                return self._finish("up_to_date", "the local card database is up to date", started, service)

            log.info("card database update at power-on: %s; the status LED blinks white until it is done",
                     check.reason)
            self.phase = "updating"
            self.led.set_updating(True)
            try:
                report = self.vision.update_database(service=service, check=check)
            except Exception as exc:
                return self._finish("unreachable" if network_error(exc) else "failed",
                                    f"update failed ({exc}): using the local card database", started, service,
                                    reason=check.reason)
            finally:
                self.led.set_updating(False)
            return self._finish("updated", str(report), started, service, reason=check.reason)
        except Exception as exc:              # never keep the machine from starting
            log.exception("card database check at power-on")
            return self._finish("failed", f"{exc}: using the local card database", started, service)

    def _wait_for_network(self) -> bool:
        if self.network_probe is None:
            return True                       # no NetworkManager to ask: just try
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                state = self.network_probe()
            except Exception:
                return True                   # could not tell: try
            if state == NET_OK:
                return True
            if state == NET_SETUP or time.monotonic() >= deadline:
                return False                  # no Wi-Fi configured, or it did not connect in time
            if self.phase != "waiting_network":
                log.info("card database check: waiting up to %.0f s for the Wi-Fi to connect", self.wait_s)
                self.phase = "waiting_network"
            if self._cancel.wait(self.poll_s):
                return False

    def _on_progress(self, stage: str, fraction: float) -> None:
        self.progress = {"stage": stage, "fraction": round(fraction, 2)}

    def _finish(self, result: str, message: str, started: float, service, reason: Optional[str] = None) -> dict:
        local = {}
        try:
            meta = (service or self.vision.sync_service()).local_metadata()
            local = {k: meta.get(k) for k in LOCAL_KEYS}
        except Exception:
            pass
        self.phase, self.progress = "done", None
        self.result = {"result": result, "message": message, "reason": reason,
                       "seconds": round(time.monotonic() - started, 1),
                       "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "local": local}
        level = logging.INFO if result in ("up_to_date", "updated") else logging.WARNING
        log.log(level, "card database at power-on: %s (%s, %.1f s)", result, message, self.result["seconds"])
        return self.result
