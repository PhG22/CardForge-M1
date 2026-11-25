"""The machine's card database (data/cards.db): check Scryfall for new cards, update when the user asks.

* Power-on (MTG_CARD_DB_CHECK): only a CHECK, in the background — 2 small requests to Scryfall; the
  machine gets ready and sorts as usual meanwhile. At boot, with Wi-Fi configured but still connecting,
  it waits up to `wait_s` for it; with no Wi-Fi configured it does not wait.
* The app is told on the WebSocket ({"type": "card_db", "card_db": status()}) and in every snapshot
  (GET /run/status, the WebSocket's first event): `update_available` makes it notify the user.
* POST /card-db/check: check again, whenever the user wants.
* POST /card-db/update: the user starts the update when it suits them (~480 MB from Scryfall, minutes
  on the Pi 3B). It runs in the background with its progress and ETA (`progress`, sent about once a
  second); the status LED blinks white and Start is refused (409 CARD_DB_UPDATING) until it ends. The old
  cards.db stays in use until the new one is complete, then they are swapped. Refused while a run is
  active (409 RUN_ACTIVE). POST /card-db/update/cancel stops it and keeps the local cards.db.

No internet, Scryfall unreachable or any failure: the machine keeps using the local cards.db.

status():
    phase             idle | checking | updating
    update_available  true / false; null = never checked successfully since the service started
    last_check        {result: up_to_date | update_available | offline | unreachable | failed, reason,
                       message, at, seconds, download_mb, remote_updated_at}
    last_update       {result: updated | up_to_date | cancelled | unreachable | failed, message, at, seconds}
    progress          while updating: {stage: checking | all_cards | default_cards | building | finishing,
                       fraction, stage_fraction, downloaded_mb, download_mb, speed_mb_s, eta_s, elapsed_s}
    local             the local cards.db: bulk_updated_at, built_at, printed_langs, schema_version, cards
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .status_led import NET_OK, NET_SETUP

log = logging.getLogger(__name__)

NETWORK_POLL_S = 2.0
FINISHING_S = 5.0                     # swap the file, reopen it, rebuild the name index
# How long building the database takes when this machine never built one (the Pi 3B is the slow case)
DEFAULT_BUILD_S = {"aarch64": 150.0, "armv7l": 200.0}
DEFAULT_BUILD_S_OTHER = 30.0


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CardDbBusy(Exception):
    """An update cannot start now (409)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UpdateProgress:
    """Progress and ETA of an update, for the app's progress bar.

    The downloads are counted in bytes, and the speed so far gives their remaining time. Building the
    database is counted in cards read; before it starts, the time the last build took on this machine
    stands in for it. The fraction is elapsed / (elapsed + remaining): the bar and the ETA always agree,
    and the bar never goes backwards."""

    def __init__(self, build_estimate_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.started = clock()
        self.build_estimate_s = build_estimate_s
        self.downloads: tuple[str, ...] = ()
        self.stage = "checking"
        self.done: dict[str, float] = {}
        self.total: dict[str, float] = {}
        self.began: dict[str, float] = {"checking": self.started}
        self.ended: dict[str, float] = {}
        self._fraction = 0.0

    def set_downloads(self, sizes: dict[str, int]) -> None:
        """What the update will download, in order: {"all_cards": bytes, "default_cards": bytes}."""
        self.downloads = tuple(stage for stage, size in sizes.items() if size)
        for stage, size in sizes.items():
            if size:
                self.total[stage] = float(size)

    def update(self, stage: str, done: float, total: float) -> bool:
        """One report from the sync. True when a new stage began."""
        now = self._clock()
        changed = stage != self.stage
        if changed:
            self.ended[self.stage] = now
            self.stage = stage
            self.began.setdefault(stage, now)
        self.done[stage] = done
        if total:
            self.total[stage] = float(total)
        return changed

    def _speed(self, now: float) -> Optional[float]:
        done = sum(self.done.get(s, 0.0) for s in self.downloads)
        seconds = sum(self.ended.get(s, now) - self.began[s] for s in self.downloads if s in self.began)
        return done / seconds if seconds >= 1.0 and done > 0 else None

    def snapshot(self) -> dict:
        now = self._clock()
        elapsed = now - self.started
        downloaded = sum(self.done.get(s, 0.0) for s in self.downloads)
        download_total = sum(self.total.get(s, 0.0) for s in self.downloads)
        speed = self._speed(now)
        eta: Optional[float] = None
        if self.stage == "finishing":
            eta = max(1.0, FINISHING_S - (now - self.began["finishing"]))
        elif self.stage == "building":
            built = self.done.get("building", 0.0) / self.total["building"] if self.total.get("building") else 0.0
            spent = now - self.began["building"]
            left = (spent * (1 - built) / built if built >= 0.05
                    else max(self.build_estimate_s - spent, 0.2 * self.build_estimate_s))
            eta = left + FINISHING_S
        elif speed is not None:
            eta = (download_total - downloaded) / speed + self.build_estimate_s + FINISHING_S
        if eta is not None and elapsed + eta > 0:
            self._fraction = max(self._fraction, min(0.99, elapsed / (elapsed + eta)))
        stage_total = self.total.get(self.stage, 0.0)
        return {
            "stage": self.stage,
            "fraction": round(self._fraction, 3),
            "stage_fraction": round(min(1.0, self.done.get(self.stage, 0.0) / stage_total), 3) if stage_total else None,
            "downloaded_mb": round(downloaded / 1e6, 1),
            "download_mb": round(download_total / 1e6, 1),
            "speed_mb_s": round(speed / 1e6, 2) if speed is not None else None,
            "eta_s": int(round(eta)) if eta is not None else None,
            "elapsed_s": int(round(elapsed)),
        }


class CardDbManager:
    def __init__(self, vision, led, controller, events=None, *, wait_s: float = 30.0,
                 network_probe: Optional[Callable[[], str]] = None, poll_s: float = NETWORK_POLL_S,
                 publish_interval_s: float = 1.0, clock: Callable[[], float] = time.monotonic) -> None:
        """vision: the cardforge.cv VisionModule (sync_service, update_database). controller: the
        SorterController (holds the machine during an update). events: the EventHub (WebSocket).
        network_probe: status_led.nmcli_network_state, or None when there is no NetworkManager."""
        self.vision = vision
        self.led = led
        self.controller = controller
        self.events = events
        self.wait_s = wait_s
        self.network_probe = network_probe
        self.poll_s = poll_s
        self.publish_interval_s = publish_interval_s
        self._clock = clock
        self._lock = threading.RLock()
        self._cancel = threading.Event()      # cancel the update in progress
        self._closing = threading.Event()     # the service is stopping
        self._thread: Optional[threading.Thread] = None
        self._last_publish = 0.0
        self.phase = "idle"
        self.update_available: Optional[bool] = None
        self.last_check: Optional[dict] = None
        self.last_update: Optional[dict] = None
        self._progress: Optional[UpdateProgress] = None
        self._sizes: dict[str, int] = {}
        self._local = self._read_local()

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        with self._lock:
            return {
                "phase": self.phase,
                "update_available": self.update_available,
                "last_check": self.last_check,
                "last_update": self.last_update,
                "progress": self._progress.snapshot() if self._progress is not None else None,
                "local": self._local,
            }

    def _publish(self, force: bool = False) -> None:
        if self.events is None:
            return
        now = self._clock()
        if not force and now - self._last_publish < self.publish_interval_s:
            return
        self._last_publish = now
        self.events.publish({"type": "card_db", "card_db": self.status()})

    def _service(self, **kw):
        return self.vision.sync_service(**kw)

    def _read_local(self) -> dict:
        try:
            service = self._service()
            meta = service.local_metadata()
            return {"bulk_updated_at": meta.get("bulk_updated_at"), "built_at": meta.get("sets_updated_at"),
                    "printed_langs": meta.get("printed_langs"), "schema_version": meta.get("schema_version"),
                    "cards": service.local_print_count() or None}
        except Exception:
            return {}

    def _spawn(self, target, *args) -> None:
        self._thread = threading.Thread(target=target, args=args, name="card-db", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ check

    def start_check(self, wait_for_network: bool = False) -> bool:
        """Check Scryfall in the background (2 small requests). False when a check or an update is
        already running. wait_for_network: at boot, wait for a configured Wi-Fi to connect."""
        with self._lock:
            if self.phase != "idle":
                return False
            self.phase = "checking"
        self._publish(force=True)             # before the thread: its result must come after this
        self._spawn(self._check, wait_for_network)
        return True

    def _check(self, wait_for_network: bool) -> None:
        started = self._clock()
        result, reason, message, extra = "failed", None, "", {}
        try:
            if wait_for_network and not self._wait_for_network():
                result, message = "offline", "no network: the local card database is used"
            else:
                check = self._service().check()
                extra = self._remember(check)
                if check.current:
                    result, message = "up_to_date", "the local card database is up to date"
                else:
                    result, reason, message = "update_available", check.reason, f"update available: {check.reason}"
        except Exception as exc:
            result = "unreachable" if network_error(exc) else "failed"
            message = f"could not check Scryfall ({exc}): the local card database is used"
            if result == "failed":
                log.exception("card database check")
        finally:
            with self._lock:
                self.last_check = {"result": result, "reason": reason, "message": message, "at": _now_iso(),
                                   "seconds": round(self._clock() - started, 1), **extra}
                if result in ("up_to_date", "update_available"):
                    self.update_available = result == "update_available"
                self.phase = "idle"
            log.log(logging.WARNING if result in ("unreachable", "failed") else logging.INFO,
                    "card database check: %s (%s)", result, message)
            self._publish(force=True)

    def _remember(self, check) -> dict:
        """Sizes of the next download (for the ETA) and what the app shows about the remote data."""
        localized = getattr(check, "localized_bulk", None)
        self._sizes = {"all_cards": localized.size if localized else 0, "default_cards": check.bulk.size}
        return {"download_mb": round(check.download_bytes / 1e6) if check.download_bytes else None,
                "remote_updated_at": check.bulk.updated_at}

    def _wait_for_network(self) -> bool:
        if self.network_probe is None:
            return True                       # no NetworkManager to ask: just try
        deadline = self._clock() + self.wait_s
        while True:
            try:
                state = self.network_probe()
            except Exception:
                return True                   # could not tell: try
            if state == NET_OK:
                return True
            if state == NET_SETUP or self._clock() >= deadline:
                return False                  # no Wi-Fi configured, or it did not connect in time
            if self._closing.wait(self.poll_s):
                return False

    # ------------------------------------------------------------------ update

    def start_update(self, force: bool = False) -> None:
        """Start the update in the background. force = rebuild even if the cards are the same.
        Raises CardDbBusy / ManualRefused (409) when it cannot start now."""
        with self._lock:
            if self.phase == "updating":
                raise CardDbBusy("CARD_DB_UPDATING", "The card database is already being updated.")
            if self.phase == "checking":
                raise CardDbBusy("CARD_DB_CHECKING",
                                 "The machine is checking for updates. Try again in a few seconds.")
            self.controller.begin_card_db_update()   # 409 RUN_ACTIVE while a run is active
            self.phase = "updating"
            self._cancel.clear()
            self._progress = UpdateProgress(self._build_estimate(), self._clock)
        self.led.set_updating(True)
        log.info("card database update started%s; the status LED blinks white until it ends",
                 " (forced)" if force else "")
        self._publish(force=True)
        self._spawn(self._update, force)

    def cancel_update(self) -> bool:
        """Stop the update in progress (the local cards.db is kept). False when none is running."""
        with self._lock:
            if self.phase != "updating":
                return False
        self._cancel.set()
        log.info("card database update: cancel requested")
        return True

    def _build_estimate(self) -> float:
        meta = {}
        try:
            meta = self._service().local_metadata()
        except Exception:
            pass
        machine = platform.machine()
        if meta.get("build_machine") == machine and meta.get("build_seconds"):
            try:
                return max(5.0, float(meta["build_seconds"]))
            except ValueError:
                pass
        return DEFAULT_BUILD_S.get(machine.lower(), DEFAULT_BUILD_S_OTHER)

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        with self._lock:
            if self._progress is None:
                return
            changed = self._progress.update(stage, done, total)
        self._publish(force=changed)

    def _update(self, force: bool) -> None:
        from cardforge.data.sync import SyncCancelled

        started = self._clock()
        result, message = "failed", ""
        try:
            service = self._service(on_progress=self._on_progress, should_stop=self._cancel.is_set)
            check = service.check()
            extra = self._remember(check)
            with self._lock:
                self._progress.set_downloads(self._sizes)
                self.update_available = not check.current
                self.last_check = {"result": "up_to_date" if check.current else "update_available",
                                   "reason": None if check.current else check.reason, "at": _now_iso(),
                                   "seconds": None, "message": "checked when the update started", **extra}
            if self._cancel.is_set():
                raise SyncCancelled()
            if check.current and not force:
                result, message = "up_to_date", "the local card database is already up to date"
            else:
                report = self.vision.update_database(force=force, service=service, check=check)
                result = "updated" if report.updated else "up_to_date"
                message = str(report)
        except SyncCancelled:
            result, message = "cancelled", "update cancelled: the local card database was kept"
        except Exception as exc:
            result = "unreachable" if network_error(exc) else "failed"
            message = f"update failed ({exc}): the local card database was kept"
            if result == "failed":
                log.exception("card database update")
        finally:
            self.led.set_updating(False)
            self.controller.end_card_db_update()
            with self._lock:
                self.last_update = {"result": result, "message": message, "at": _now_iso(),
                                    "seconds": round(self._clock() - started, 1)}
                if result in ("updated", "up_to_date"):
                    self.update_available = False
                    if self.last_check is not None:
                        self.last_check = {**self.last_check, "result": "up_to_date", "reason": None}
                    self._local = self._read_local()
                self._progress = None
                self.phase = "idle"
            log.log(logging.WARNING if result in ("unreachable", "failed") else logging.INFO,
                    "card database update: %s (%s, %.0f s)", result, message, self._clock() - started)
            self._publish(force=True)

    # ------------------------------------------------------------------ service stop

    def close(self) -> None:
        """The service is stopping: stop waiting for the network and cancel an update in progress."""
        self._closing.set()
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
