"""Run controller: the Raspberry Pi main loop (FW-AD-01).

States:  IDLE -> STARTING -> RUNNING -> (STOPPING) -> IDLE
         STARTING -> IDLE when the start checks fail (start() raises StartRefused)
         RUNNING -> PAUSED on a mechanical fault -> (resume) STARTING -> RUNNING
                                                 -> (stop)   IDLE

One card (FW-SD-02):  feed (A1) -> identify (camera + vision, up to 3 captures)
                      -> tray from the color identity -> base to the tray (A2)
                      -> drop (A1 flap) -> one record in run.db -> progress event

A run ends when the stack is empty or 250 cards were processed (completed), when
the user stops it (cancelled), or on a fault that needs a power cycle (failed:
a module stopped answering = Data-Link Loss). Mechanical faults (feed jam, a card
that does not fall, the base not reaching a tray) PAUSE the run instead (RNF23):
the motors stop, the run keeps its id and counters, the app is alerted (RF27),
and /run/resume re-homes the base and continues the same run (FW-SD-04).

Two kinds of stop:
  * normal (default): finish the feed in progress, send the card on the platform
    to its tray (or to the overflow tray 6 if it was not identified yet), drop it,
    record it, close the flap, then stop all motors. No card is left in the machine.
  * immediate: stop all motors right now. A card may stay on the platform.

While a run is active the live events carry progress only, never which card was
read or where it went (RF23). Results are read from run.db once the run is over.

With settings.bottom_enabled = False (top-only test bench) the bottom module A2
is never contacted: no homing, no tray check, no base moves.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .bus import BusError, ModuleError, ModuleNotResponding
from .config import Settings
from .events import EventHub
from .identifier import FACE_DOWN, IDENTIFIED, CardIdentifier, Identification
from .modules import BottomModule, ModuleState, TopModule
from .protocol import FrameError
from .sorting import OVERFLOW_TRAY
from .store import RunStore, ScanRecord

log = logging.getLogger(__name__)


def utc_now() -> str:
    # Same format as the local SQLite schema: 2026-10-21T22:19:00.123Z
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ControllerState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"            # a mechanical fault holds the run until /run/resume or /run/stop
    MANUAL = "manual"            # a /base/... test move is running


class RunStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Problem:
    code: str
    message: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


class StartRefused(Exception):
    def __init__(self, http_status: int, problems: list[Problem]) -> None:
        self.http_status = http_status
        self.problems = problems
        super().__init__("; ".join(p.message for p in problems))

    @property
    def message(self) -> str:
        return " ".join(p.message for p in self.problems)


class NoActiveRun(Exception):
    pass


class NotPaused(Exception):
    pass


class ManualRefused(Exception):
    def __init__(self, http_status: int, code: str, message: str) -> None:
        self.http_status = http_status
        self.code = code
        self.message = message
        super().__init__(message)


class Aborted(Exception):
    """Immediate stop: raised from any wait in the loop."""


class StopRequested(Exception):
    """Normal stop: raised only where the loop can safely switch to clearing out."""


class HardwareFault(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class RunLimitReached(Exception):
    """RNF27: the batch reached its maximum number of cards."""


@dataclass
class RunInfo:
    run_id: str                     # UUIDv4 = local_run.local_uuid
    status: RunStatus
    started_at: str
    stopped_at: str | None = None
    cards_processed: int = 0
    current_tray: int | None = None
    error_code: str | None = None
    message: str | None = None
    fault_at: str | None = None     # when the run was paused by a fault

    def to_dict(self) -> dict:
        active = self.status in (RunStatus.RUNNING, RunStatus.PAUSED)
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "cards_processed": self.cards_processed,
            # The tray of the last card tells its category: hidden while the run is active (RF23).
            "current_tray": None if active else self.current_tray,
            "error_code": self.error_code,
            "message": self.message,
            "fault_at": self.fault_at,
        }


class SorterController:
    def __init__(
        self,
        top: TopModule,
        bottom: BottomModule,
        identifier: CardIdentifier,
        events: EventHub,
        settings: Settings,
        store: RunStore | None = None,
    ) -> None:
        self.top = top
        self.bottom = bottom
        self.identifier = identifier
        self.events = events
        self.s = settings
        self.store = store
        self._lock = threading.Lock()
        self._state = ControllerState.IDLE
        self._stop_requested = threading.Event()   # normal stop (also set by an immediate stop)
        self._abort = threading.Event()            # immediate stop
        self._thread: threading.Thread | None = None
        self._run: RunInfo | None = None           # current or last run
        self._in_progress: Identification | None = None   # identified card not dropped yet
        self._optional_ok = {"LIGHT": True, "LED": True}  # firmware supports the optional commands?
        if store is not None:
            n = store.recover_interrupted(utc_now())
            if n:
                log.warning("%d run(s) were interrupted by a restart and are now marked failed", n)

    @property
    def _modules(self) -> tuple:
        return (self.top, self.bottom) if self.s.bottom_enabled else (self.top,)

    # ------------------------------------------------------------------ API

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self._state.value,
                "run": self._run.to_dict() if self._run else None,
            }

    @property
    def state(self) -> ControllerState:
        with self._lock:
            return self._state

    def start(self) -> RunInfo:
        """Prepare the modules and check the start conditions; if they pass,
        launch the sorting loop in the background and return."""
        with self._lock:
            if self._state is not ControllerState.IDLE:
                raise StartRefused(409, [Problem(
                    "RUN_ACTIVE", f"Cannot start: the sorter is busy ({self._state.value}).")])
            self._state = ControllerState.STARTING
            self._stop_requested = threading.Event()
            self._abort = threading.Event()

        problems = self._guarded_prepare(home=True, idle_on_fail=True)
        if problems:
            self._set_idle()
            self._led("STARTUP_BLOCKED")
            raise StartRefused(409, problems)

        run = RunInfo(run_id=str(uuid.uuid4()), status=RunStatus.RUNNING, started_at=utc_now(),
                      message="Run started.")
        self.identifier.reset()
        with self._lock:
            if self._stop_requested.is_set():          # stop arrived right after the checks
                self._state = ControllerState.IDLE
                raise StartRefused(409, [Problem("START_CANCELLED", "Start was cancelled by a stop request.")])
            if self.store is not None:
                self.store.create_run(run.run_id, run.started_at)
            self._run = run
            self._launch(run)
        self.events.publish({"type": "run_started", "run": run.to_dict()})
        return run

    def resume(self) -> RunInfo:
        """Continue a run paused by a fault (FW-SD-04): re-home, re-check, keep the same run."""
        with self._lock:
            if self._state is not ControllerState.PAUSED or self._run is None:
                raise NotPaused()
            run = self._run
            self._state = ControllerState.STARTING
            self._stop_requested = threading.Event()
            self._abort = threading.Event()

        problems = self._guarded_prepare(home=True, idle_on_fail=False, resuming=True)
        if problems:
            with self._lock:
                self._state = ControllerState.PAUSED
            raise StartRefused(409, problems)
        with self._lock:
            if self._stop_requested.is_set():          # stop arrived right after the checks
                self._state = ControllerState.PAUSED
                raise StartRefused(409, [Problem("START_CANCELLED", "Resume was cancelled by a stop request.")])
            run.status = RunStatus.RUNNING
            run.error_code = None
            run.fault_at = None
            run.message = "Run resumed."
            if self.store is not None:
                self.store.update_run(run.run_id, status=RunStatus.RUNNING.value, message=run.message)
            self._launch(run)
        self.events.publish({"type": "run_resumed", "run": run.to_dict()})
        return run

    def stop(self, immediate: bool = False) -> tuple[str, dict]:
        """End the current run (status 'cancelled'). Returns (message for the app, snapshot).

        Normal stop waits until the card in progress has been dropped and the
        motors are stopped (a few seconds). Immediate stop halts the motors at once.
        A stop during the start checks always cancels the start.
        """
        with self._lock:
            previous = self._state
            if previous is ControllerState.IDLE:
                raise NoActiveRun()
            if previous is ControllerState.PAUSED:
                run = self._run
            else:
                run = None
                if previous is ControllerState.RUNNING:
                    self._state = ControllerState.STOPPING
                immediate = immediate or previous in (ControllerState.STARTING, ControllerState.MANUAL)
                self._stop_requested.set()
                if immediate:
                    self._abort.set()
            thread = self._thread
        if run is not None:                          # paused: motors are already stopped
            try:
                self.top.set_flap(self.s.flap_closed_angle)
            except (BusError, FrameError) as exc:
                log.warning("could not close the flap: %s", exc)
            self._finish(run, RunStatus.CANCELLED, None,
                         f"Run stopped by user while paused, after {run.cards_processed} card(s).")
            return run.message, self.snapshot()
        if immediate:
            self._safe_stop_modules()   # don't wait for the loop to notice
        if thread is not None:
            thread.join(timeout=3.0 if immediate else self._normal_stop_budget())
        snapshot = self.snapshot()
        if previous is ControllerState.STARTING:
            message = "Start cancelled."
        elif previous is ControllerState.MANUAL:
            message = "Base stopped."
        elif snapshot["state"] == ControllerState.IDLE.value and snapshot["run"]:
            message = snapshot["run"]["message"]
        else:
            message = "Stopping: finishing the card in progress."
        return message, snapshot

    # ------------------------------------------------------- manual base moves

    def base_status(self) -> dict:
        self._require_bottom()
        return self.bottom.status().to_dict()

    def base_home(self) -> dict:
        ms = int(self.s.home_timeout_s * 1000)
        return self._manual(lambda: self.bottom.start_home(ms), self.s.home_timeout_s + 2.0,
                            "HOME_TIMEOUT", "The base did not find the strip in time.",
                            poll_s=self.s.home_poll_interval_s)

    def base_goto(self, tray: int) -> dict:
        return self._manual(lambda: self.bottom.start_goto(tray), self.s.move_timeout_s,
                            "MOVE_TIMEOUT", f"The base did not reach tray {tray}.")

    def base_move(self, steps: int) -> dict:
        return self._manual(lambda: self.bottom.start_move(steps), self.s.move_timeout_s,
                            "MOVE_TIMEOUT", f"The base did not reach position {steps}.")

    def _require_bottom(self) -> None:
        if not self.s.bottom_enabled:
            raise ManualRefused(409, "BOTTOM_DISABLED",
                                "The bottom module is disabled (top-only mode). Restart the API without --top-only.")

    def _manual(self, start_action, timeout_s: float, timeout_code: str, timeout_message: str,
                poll_s: float | None = None) -> dict:
        """Run one base action outside a run and wait until it finishes."""
        self._require_bottom()
        with self._lock:
            if self._state is not ControllerState.IDLE:
                raise ManualRefused(409, "RUN_ACTIVE",
                                    f"The sorter is busy ({self._state.value}). Stop the run first.")
            self._state = ControllerState.MANUAL
        try:
            ms = start_action()
            poll_s = self.s.base_poll_interval_s if poll_s is None else poll_s
            deadline = time.monotonic() + timeout_s
            time.sleep(min(_quiet_time(ms), timeout_s))   # leave the bus quiet while it moves
            while True:
                status = self.bottom.status()
                if status.state == ModuleState.IDLE:
                    return status.to_dict()
                if status.state == ModuleState.ERROR:
                    raise HardwareFault(status.error or "MODULE_ERROR",
                                        _error_message(self.bottom.ADDRESS, status.error))
                if time.monotonic() > deadline:
                    self._safe_stop_modules()
                    raise HardwareFault(timeout_code, timeout_message)
                time.sleep(poll_s)
        finally:
            self._set_idle()

    # ------------------------------------------- camera calibration helpers (no run)

    def calibration_feed(self) -> dict:
        """Feed one card onto the platform and leave it there, for the camera calibration."""
        s = self.s
        with self._manual_session():
            if self.top.status().platform_has_card:
                return {"message": "A card is already on the platform.", "platform_has_card": True}
            self.top.set_flap(s.flap_closed_angle)
            self.top.start_feed(int(s.feed_timeout_s * 1000))
            top = self._wait_idle(self.top, s.feed_timeout_s + 2.0, "FEED_TIMEOUT",
                                  "The card did not reach the platform.")
            return {"message": "Card on the platform." if top.platform_has_card else "No card reached the platform.",
                    "platform_has_card": top.platform_has_card}

    def calibration_drop(self) -> dict:
        """Drop the calibration card into the overflow tray 6 (homes the base first if needed)."""
        s = self.s
        with self._manual_session():
            if not self.top.status().platform_has_card:
                return {"message": "No card on the platform.", "tray": None}
            tray = None
            if s.bottom_enabled:
                if not self.bottom.status().homed:
                    self.bottom.start_home(int(s.home_timeout_s * 1000))
                    self._wait_idle(self.bottom, s.home_timeout_s + 2.0, "HOME_TIMEOUT",
                                    "The base did not reach the zero position.", poll_s=s.home_poll_interval_s)
                tray = OVERFLOW_TRAY
            self._route_and_drop(OVERFLOW_TRAY)
            return {"message": f"Card dropped into tray {tray}." if tray else "Card dropped.", "tray": tray}

    def require_idle(self) -> None:
        with self._lock:
            if self._state is not ControllerState.IDLE:
                raise ManualRefused(409, "RUN_ACTIVE",
                                    f"The sorter is busy ({self._state.value}). Stop the run first.")

    @contextmanager
    def _manual_session(self):
        """MANUAL state for a short hardware action outside a run; /run/stop aborts it."""
        with self._lock:
            if self._state is not ControllerState.IDLE:
                raise ManualRefused(409, "RUN_ACTIVE",
                                    f"The sorter is busy ({self._state.value}). Stop the run first.")
            self._state = ControllerState.MANUAL
            self._stop_requested = threading.Event()
            self._abort = threading.Event()
        try:
            yield
        except Aborted:
            raise ManualRefused(409, "CANCELLED", "Cancelled by a stop request.")
        finally:
            self._set_idle()

    def stack_status(self) -> dict:
        """Ask A1's stack IR whether there are cards to sort."""
        has_cards = self.top.status().stack_has_card
        return {
            "has_cards": has_cards,
            "message": "There are cards on the stack." if has_cards else "No cards on the stack.",
        }

    def hardware_status(self) -> dict:
        result = {}
        for name, module in (("top", self.top), ("bottom", self.bottom)):
            if module is self.bottom and not self.s.bottom_enabled:
                result[name] = {"online": False, "enabled": False,
                                "error": "bottom module disabled (top-only mode)"}
                continue
            try:
                result[name] = {"online": True, "enabled": True, **module.status().to_dict()}
            except (BusError, FrameError) as exc:
                result[name] = {"online": False, "enabled": True, "error": str(exc)}
        return result

    def power_on(self) -> None:
        """Service start: chamber light to its base level, status LED to Idle (RF16)."""
        try:
            self._light(self.s.light_base_level)
            self._led("IDLE")
        except (BusError, FrameError) as exc:
            log.warning("modules not reachable at power-on: %s", exc)

    def shutdown(self) -> None:
        try:
            self.stop(immediate=True)
        except NoActiveRun:
            pass

    # ------------------------------------------------------- start sequence

    def _guarded_prepare(self, *, home: bool, idle_on_fail: bool, resuming: bool = False) -> list[Problem]:
        """_prepare() with the hardware errors turned into StartRefused."""
        def fail(status: int, problems: list[Problem]):
            self._safe_stop_modules()
            if idle_on_fail:
                self._set_idle()
            else:
                with self._lock:
                    self._state = ControllerState.PAUSED
            return StartRefused(status, problems)

        try:
            return self._prepare(home=home, resuming=resuming)
        except Aborted:
            if idle_on_fail:
                self._set_idle()
            else:
                with self._lock:
                    self._state = ControllerState.PAUSED
            raise StartRefused(409, [Problem("START_CANCELLED", "Start was cancelled by a stop request.")])
        except ModuleNotResponding as exc:
            raise fail(503, [Problem(
                "MODULE_OFFLINE",
                f"The {_module_name(exc.addr)} module ({exc.addr}) is not responding. "
                "Check its power and the RS-485 cable.",
                {"module": exc.addr})])
        except (HardwareFault, ModuleError, FrameError) as exc:
            code, message = _describe_fault(exc)
            raise fail(500, [Problem(code, message)])
        except Exception:
            log.exception("unexpected error while starting")
            self._safe_stop_modules()
            if idle_on_fail:
                self._set_idle()
            raise

    def _prepare(self, *, home: bool, resuming: bool = False) -> list[Problem]:
        s = self.s
        self._check_abort()

        # Quick checks first (< 1 s), so a refused start answers before the long homing.
        problems = self._check_conditions(self.top.status(),
                                          self.bottom.status() if s.bottom_enabled else None,
                                          resuming=resuming)
        if problems:
            return problems

        # Bottom first: homing takes the longest and runs on A2 by itself,
        # while we set up A1 over the same bus.
        if s.bottom_enabled and home:
            self.bottom.start_home(int(s.home_timeout_s * 1000))

        self.top.roller_stop()                    # feed roller must not rotate
        self.top.set_flap(s.flap_closed_angle)    # flap closed (80 degrees)
        self._light(s.light_base_level)
        self._sleep(s.flap_settle_s)

        bottom = None
        if s.bottom_enabled and home:
            bottom = self._wait_idle(self.bottom, s.home_timeout_s + 2.0, "HOME_TIMEOUT",
                                     "The base did not reach the zero position.", poll_s=s.home_poll_interval_s)
        # Checked again after homing: someone may have touched the machine meanwhile.
        return self._check_conditions(self.top.status(), bottom, resuming=resuming)

    def _check_conditions(self, top, bottom, resuming: bool = False) -> list[Problem]:
        problems: list[Problem] = []
        if not resuming and hasattr(self.identifier, "problems"):
            problems += [Problem(code, message) for code, message in self.identifier.problems()]
        if not top.stack_has_card and not resuming:
            problems.append(Problem("STACK_EMPTY", "No cards on the stack. Load cards and try again."))
        # A resumed run keeps the cards it already sorted in the trays (RNF23).
        if bottom is not None and bottom.trays_with_cards and not resuming:
            trays = ", ".join(str(t) for t in bottom.trays_with_cards)
            problems.append(Problem(
                "TRAYS_NOT_EMPTY", f"Trays are not empty (tray {trays}). Empty them and try again.",
                {"trays": list(bottom.trays_with_cards)}))
        if top.platform_has_card:
            problems.append(Problem(
                "PLATFORM_NOT_CLEAR", "There is a card on the reading platform. Remove it and try again."))
        return problems

    # ------------------------------------------------------------- run loop

    def _launch(self, run: RunInfo) -> None:
        """Called with self._lock held."""
        self._state = ControllerState.RUNNING
        self._thread = threading.Thread(target=self._run_loop, args=(run,), name="sorter-run", daemon=True)
        self._thread.start()

    def _run_loop(self, run: RunInfo) -> None:
        log.info("run %s running", run.run_id)
        self._led("RUNNING")
        try:
            stopped = limit = False
            try:
                while True:
                    self._check_abort()
                    if self._stop_requested.is_set():
                        stopped = True
                        break
                    if run.cards_processed >= self.s.max_cards_per_run:
                        limit = True
                        break
                    if not self.top.status().stack_has_card:
                        break
                    self._sort_one_card(run)
            except StopRequested:
                stopped = True

            if stopped:
                cleared = self._clear_out(run)
                self._safe_stop_modules()
                n = run.cards_processed
                if cleared:
                    message = (f"Run stopped by user: the card in progress was sorted, {n} card(s) "
                               "processed. Flap closed, motors stopped.")
                else:
                    message = f"Run stopped by user after {n} card(s). Flap closed, motors stopped."
                self._finish(run, RunStatus.CANCELLED, None, message)
            elif limit:
                self._safe_stop_modules()
                self._finish(run, RunStatus.COMPLETED, "BATCH_LIMIT",
                             f"Batch limit reached: {run.cards_processed} card(s) processed. "
                             "Empty the trays and start a new run for the remaining cards.")
            else:
                self._finish(run, RunStatus.COMPLETED, None,
                             f"Stack is empty: {run.cards_processed} card(s) processed.")
        except Aborted:
            self._safe_stop_modules()
            self._finish(run, RunStatus.CANCELLED, None,
                         f"Run stopped immediately by user after {run.cards_processed} card(s). "
                         "A card may still be on the platform.")
        except ModuleNotResponding as exc:
            # Data-Link Loss (Appendix C): needs a power cycle, the run cannot be resumed.
            self._safe_stop_modules()
            self._led("FEED_JAM")
            self._finish(run, RunStatus.FAILED, "MODULE_OFFLINE",
                         f"The {_module_name(exc.addr)} module ({exc.addr}) stopped responding. "
                         "Turn the machine off and on again.")
        except FrameError as exc:
            self._safe_stop_modules()
            self._led("FEED_JAM")
            self._finish(run, RunStatus.FAILED, "PROTOCOL_ERROR", f"Invalid reply from a module: {exc}")
        except (HardwareFault, ModuleError) as exc:
            self._safe_stop_modules()
            code, message = _describe_fault(exc)
            self._pause(run, code, message)
        except Exception:
            log.exception("run loop crashed")
            self._safe_stop_modules()
            self._finish(run, RunStatus.FAILED, "INTERNAL_ERROR", "Unexpected error in the sorter software.")

    def _sort_one_card(self, run: RunInfo) -> None:
        s = self.s
        card_number = run.cards_processed + 1

        # 1. Feed one card from the stack onto the platform.
        #    A normal stop does not interrupt this: the card is in the feeder.
        self._check_abort()
        self.top.start_feed(int(s.feed_timeout_s * 1000))
        top = self._wait_idle(self.top, s.feed_timeout_s + 2.0, "FEED_TIMEOUT",
                              "The card did not reach the platform.")
        if not top.platform_has_card:
            raise HardwareFault("FEED_FAILED", "Feeding finished but no card is on the platform.")

        # 2. Take pictures and identify it. A normal stop ends the retries early;
        #    _clear_out() then sends the card to its tray (or to the overflow tray).
        if self._stop_requested.is_set():
            raise StopRequested()
        result = self.identifier.identify(self._sleep_unless_stopped)
        self._in_progress = result
        if self._stop_requested.is_set():
            raise StopRequested()

        # 3. Turn the base so the target tray is under the platform, 4. drop the card.
        tray = self._route_and_drop(result.tray)
        self._record(run, card_number, result, tray)

    def _route_and_drop(self, tray: int) -> int | None:
        s = self.s
        if s.bottom_enabled:
            ms = self.bottom.start_goto(tray)
            self._wait_idle(self.bottom, s.move_timeout_s, "MOVE_TIMEOUT",
                            f"The base did not reach tray {tray}.",
                            poll_s=s.base_poll_interval_s, quiet_s=_quiet_time(ms))
        self._drop_card()
        return tray if s.bottom_enabled else None

    def _record(self, run: RunInfo, card_number: int, result: Identification, tray: int | None,
                error_message: str | None = None) -> None:
        """The card left the platform: one record in run.db (RNF20), then a progress event."""
        details = dict(result.details)
        if error_message:
            details["error_message"] = error_message
        if self.store is not None:
            self.store.save_scan(run.run_id, card_number, utc_now(), ScanRecord(
                scan_status=result.scan_status, bin=tray, card_identifier=result.card_identifier,
                attempts=result.attempts, **{k: v for k, v in details.items() if k in _RECORD_FIELDS}))
        self._in_progress = None
        with self._lock:
            run.cards_processed = card_number
            run.current_tray = tray
        # RF23: progress only. No card identity, no tray.
        self.events.publish({"type": "card_sorted", "run_id": run.run_id, "card_number": card_number})
        if result.scan_status != IDENTIFIED and result.notify:
            # Attention event (RF36): logged and notified, the run continues.
            self.events.publish({
                "type": "attention",
                "kind": "face_down" if result.scan_status == FACE_DOWN else "unidentified",
                "run_id": run.run_id, "card_number": card_number, "time": utc_now(),
            })

    def _clear_out(self, run: RunInfo) -> bool:
        """Normal stop: leave no card in the machine. Returns True if a card was sorted."""
        s = self.s
        self._check_abort()
        if s.bottom_enabled:
            bottom = self.bottom.status()
            if bottom.state == ModuleState.BUSY:
                self._wait_idle(self.bottom, s.move_timeout_s, "MOVE_TIMEOUT", "The base did not stop.",
                                poll_s=s.base_poll_interval_s)
        top = self.top.status()
        if top.state == ModuleState.BUSY:          # a feed still running: let the card arrive
            top = self._wait_idle(self.top, s.feed_timeout_s + 2.0, "FEED_TIMEOUT",
                                  "The card did not reach the platform.")
        cleared = False
        if top.platform_has_card:
            # Identified before the stop: it still goes to its own tray. Otherwise: overflow.
            result = self._in_progress or Identification(tray=OVERFLOW_TRAY)
            note = None if self._in_progress else "run stopped before the card was identified"
            card_number = run.cards_processed + 1
            tray = self._route_and_drop(result.tray)
            self._record(run, card_number, result, tray, error_message=note)
            cleared = True
            self.events.publish({"type": "card_cleared", "run_id": run.run_id, "card_number": card_number})
        else:
            self.top.set_flap(s.flap_closed_angle)
            self._sleep(s.flap_settle_s)
        self._in_progress = None
        self.top.roller_stop()
        return cleared

    def _drop_card(self) -> None:
        """Same sequence as feeder_flap_test.ino: open, wait until the platform IR
        is clear, give the card time to fall, close."""
        s = self.s
        self._check_abort()
        self.top.set_flap(s.flap_open_angle)
        deadline = time.monotonic() + s.drop_timeout_s
        while self.top.status().platform_has_card:
            if time.monotonic() > deadline:
                raise HardwareFault("DROP_FAILED", "The card did not fall from the platform (stuck?).")
            self._sleep(s.poll_interval_s)
        self._sleep(s.card_fall_s)
        self.top.set_flap(s.flap_closed_angle)
        self._sleep(s.flap_settle_s)

    # -------------------------------------------------------------- helpers

    def _normal_stop_budget(self) -> float:
        s = self.s
        return (s.feed_timeout_s + 2.0 + s.drop_timeout_s + s.card_fall_s + 2 * s.flap_settle_s
                + (2 * s.move_timeout_s if s.bottom_enabled else 0.0) + 3.0)

    def _wait_idle(self, module, timeout_s: float, timeout_code: str, timeout_message: str,
                   poll_s: float | None = None, quiet_s: float = 0.0):
        """Poll STATUS until the module is IDLE. ERROR -> HardwareFault.
        quiet_s: first wait this long without talking to the module (base moves)."""
        deadline = time.monotonic() + timeout_s
        poll_s = self.s.poll_interval_s if poll_s is None else poll_s
        if quiet_s > 0:
            self._sleep(min(quiet_s, timeout_s))
        while True:
            status = module.status()
            if status.state == ModuleState.IDLE:
                return status
            if status.state == ModuleState.ERROR:
                raise HardwareFault(status.error or "MODULE_ERROR", _error_message(module.ADDRESS, status.error))
            if time.monotonic() > deadline:
                raise HardwareFault(timeout_code, timeout_message)
            self._sleep(poll_s)

    def _sleep(self, seconds: float) -> None:
        """Wait; only an immediate stop interrupts it."""
        if self._abort.wait(seconds):
            raise Aborted()

    def _sleep_unless_stopped(self, seconds: float) -> None:
        """Wait; any stop interrupts it."""
        if self._stop_requested.wait(seconds):
            self._check_abort()
            raise StopRequested()

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise Aborted()

    def _safe_stop_modules(self) -> None:
        for module in self._modules:
            try:
                module.stop()
            except (BusError, FrameError) as exc:
                log.warning("could not send STOP to %s: %s", module.ADDRESS, exc)

    def _optional(self, command: str, call) -> None:
        """LIGHT / LED are optional firmware commands: turned off for good on ERR BAD_CMD."""
        if not self._optional_ok[command]:
            return
        try:
            call()
        except ModuleError as exc:
            if exc.code == "BAD_CMD":
                log.warning("A1 firmware has no %s command: feature disabled", command)
                self._optional_ok[command] = False
            else:
                log.warning("%s failed: %s", command, exc)
        except (BusError, FrameError) as exc:
            log.warning("%s failed: %s", command, exc)

    def _light(self, level: int) -> None:
        self._optional("LIGHT", lambda: self.top.set_light(level))

    def _led(self, mode: str) -> None:
        self._optional("LED", lambda: self.top.set_led(mode))

    def set_chamber_light(self, level: int) -> None:
        """Used by the vision identifier to raise the light before a retry (RNF09)."""
        self._light(level)

    def _set_idle(self) -> None:
        with self._lock:
            self._state = ControllerState.IDLE

    def _pause(self, run: RunInfo, code: str, message: str) -> None:
        """Mechanical fault: hold the run (RNF23) until /run/resume or /run/stop."""
        now = utc_now()
        with self._lock:
            run.status = RunStatus.PAUSED
            run.error_code = code
            run.message = message + " Fix it, then press Resume."
            run.fault_at = now
            self._state = ControllerState.PAUSED
            self._thread = None
        self._in_progress = None
        self._led("FEED_JAM")
        if self.store is not None:
            self.store.update_run(run.run_id, status=RunStatus.PAUSED.value, error_code=code, message=run.message)
        log.warning("run %s paused: %s %s", run.run_id, code, message)
        self.events.publish({"type": "fault", "fault": code, "time": now, "message": run.message,
                             "run": run.to_dict()})

    def _finish(self, run: RunInfo, status: RunStatus, error_code: str | None, message: str) -> None:
        with self._lock:
            run.status = status
            run.stopped_at = utc_now()
            run.error_code = error_code
            run.message = message
            self._state = ControllerState.IDLE
            self._thread = None
        self._in_progress = None
        if self.store is not None:
            self.store.update_run(run.run_id, status=status.value, stopped_at=run.stopped_at,
                                  error_code=error_code, message=message)
        if status is not RunStatus.FAILED:
            self._led("IDLE")
        log.info("run %s %s: %s", run.run_id, status.value, message)
        self.events.publish({"type": "run_finished", "run": run.to_dict(), "message": message})


_RECORD_FIELDS = {"match_method", "confidence", "card_name", "set_code", "collector_number", "color_identity",
                  "type_line", "mana_cost", "rarity", "error_message"}

_MODULE_NAMES = {TopModule.ADDRESS: "top", BottomModule.ADDRESS: "bottom"}

_ERROR_MESSAGES = {
    "FEED_TIMEOUT": "The feeder could not move a card onto the platform (jam or empty stack).",
    "FEED_FAILED": "Feeding finished but no card is on the platform.",
    "DROP_FAILED": "The card did not fall from the platform (stuck?).",
    "HOME_TIMEOUT": "The base could not find the zero position. Check the zero IR sensor.",
    "HOME_FAILED": "The base could not find the reflective strip. Check the homing IR sensor.",
    "MOVE_TIMEOUT": "The base did not reach the requested tray.",
    "NOT_HOMED": "The base has not been homed.",
}


def _quiet_time(duration_ms: int | None) -> float:
    """Seconds to wait before the first STATUS poll after a base move (2 % margin)."""
    return 0.0 if not duration_ms else duration_ms * 1.02 / 1000 + 0.05


def _module_name(addr: str) -> str:
    return _MODULE_NAMES.get(addr, addr)


def _error_message(addr: str, code: str | None) -> str:
    return _ERROR_MESSAGES.get(code or "", f"The {_module_name(addr)} module ({addr}) reported error {code}.")


def _describe_fault(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, HardwareFault):
        return exc.code, exc.message
    if isinstance(exc, ModuleError):
        return exc.code, f"The {_module_name(exc.addr)} module ({exc.addr}) rejected '{exc.command}': {exc.code}."
    return "PROTOCOL_ERROR", f"Invalid reply from a module: {exc}"
