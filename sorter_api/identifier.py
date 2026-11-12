"""Decides which tray a card goes to.

* VisionIdentifier: the real one. Takes a picture with the camera and identifies
  the card with the computer-vision module (cardforge.cv), up to 3 captures per
  card (RF29), then picks the tray from the color identity (RF20). Unidentified
  (RF25) and face-down (RF39) cards go to the overflow tray 6.
* PlaceholderIdentifier: no camera. Waits (as if a picture were being taken)
  and sends the cards to trays 1, 2, ... 6, 1, 2, ... in turn. Used for bench
  tests of the mechanics (MTG_VISION=off).

The run loop only needs identify() to return an Identification.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from .sorting import OVERFLOW_TRAY, tray_for_color_identity

log = logging.getLogger(__name__)

IDENTIFIED = "identified"
UNIDENTIFIED = "unidentified"
FACE_DOWN = "face_down"


@dataclass(frozen=True)
class Identification:
    tray: int
    card_identifier: str | None = None   # Scryfall print id
    scan_status: str = UNIDENTIFIED      # identified | unidentified | face_down
    attempts: int = 0
    details: dict = field(default_factory=dict)   # name, set, number, colors, method, confidence...
    notify: bool = True                  # False = no attention event (placeholder: nothing was scanned)


class CardIdentifier(Protocol):
    def reset(self) -> None: ...
    def identify(self, sleep: Callable[[float], None]) -> Identification: ...


class PlaceholderIdentifier:
    def __init__(self, delay_s: float = 2.0, trays: int = 6) -> None:
        self.delay_s = delay_s
        self.trays = trays
        self._next_tray = 1

    def reset(self) -> None:
        self._next_tray = 1

    def problems(self) -> list[tuple[str, str]]:
        return []

    def identify(self, sleep: Callable[[float], None]) -> Identification:
        # `sleep` is the run loop's interruptible sleep: a stop request ends it early.
        sleep(self.delay_s)
        tray = self._next_tray
        self._next_tray = tray % self.trays + 1
        return Identification(tray=tray, details={"error_message": "camera disabled (placeholder identifier)"},
                              notify=False)


class VisionIdentifier:
    """
    One card = up to `attempts` captures (RF29: 1 + 2 retries). Before each retry the
    chamber light is raised one step (RNF09) through `set_light`, if the hardware
    supports it. The vision module itself never touches the network (RNF15).
    """

    def __init__(self, vision, attempts: int = 3, light_base: int = 60, light_step: int = 20,
                 set_light: Optional[Callable[[int], None]] = None) -> None:
        self.vision = vision                    # cardforge.cv.VisionModule (or a test double)
        self.attempts = max(1, attempts)
        self.light_base = light_base
        self.light_step = light_step
        self.set_light = set_light

    def reset(self) -> None:
        self._light(self.light_base)

    def problems(self) -> list[tuple[str, str]]:
        """Pre-run block (CV-SD-04): no card database = no identification."""
        problems = []
        if not getattr(self.vision, "ready", True):
            problems.append(("CARD_DB_MISSING", "The card database is missing on the machine. "
                                                "Run 'python -m cardforge.cv sync' on the Raspberry Pi."))
        crop = getattr(getattr(self.vision, "cfg", None), "crop", None)
        if crop is not None and crop.mode == "fixed" and len(crop.corners) != 4:
            problems.append(("CROP_NOT_CALIBRATED", "The camera crop is not calibrated. Open "
                                                    "http://<machine-ip>:8000/calibration and save the fixed crop."))
        return problems

    def identify(self, sleep: Callable[[float], None]) -> Identification:
        from cardforge.cv import IdentificationStatus

        result, attempt, level, last_error = None, 0, self.light_base, ""
        slowest_capture_ms: int | None = None
        try:
            for attempt in range(1, self.attempts + 1):
                began = time.perf_counter()
                if attempt > 1:
                    sleep(0)                    # a stop request ends the retries here
                    level = min(100, level + self.light_step)
                    self._light(level)
                called = time.perf_counter()
                try:
                    result = self.vision.identify()
                except Exception as exc:        # camera/vision fault: count it as a failed capture
                    log.exception("vision identify() failed")
                    last_error = str(exc)
                    continue
                # RF02 / RF15: the capture routine = (light change for a retry) + the photo.
                capture_ms = getattr(result, "debug", {}).get("capture_ms")
                if isinstance(capture_ms, int):
                    routine_ms = int((called - began) * 1000) + capture_ms
                    slowest_capture_ms = max(slowest_capture_ms or 0, routine_ms)
                log.info("capture %d: %s %s%% %s (photo %s ms, recognition %s ms)", attempt, result.status.value,
                         result.confidence, result.card.name if result.card else "-", capture_ms,
                         getattr(result, "elapsed_ms", None))
                if result.status.is_final:
                    break
        finally:
            if level != self.light_base:
                self._light(self.light_base)

        if result is None:
            return Identification(OVERFLOW_TRAY, None, UNIDENTIFIED, attempt,
                                  {"error_message": f"vision error: {last_error}"})
        details = _details(result)
        if slowest_capture_ms is not None:
            details["capture_ms"] = slowest_capture_ms
        if result.status is IdentificationStatus.IDENTIFIED and result.card is not None:
            tray = tray_for_color_identity(result.card.color_identity)
            return Identification(tray, result.card.print_id, IDENTIFIED, attempt, details)
        if result.status is IdentificationStatus.FACE_DOWN:
            return Identification(OVERFLOW_TRAY, None, FACE_DOWN, attempt, details)
        details["error_message"] = f"{result.status.value} after {attempt} capture(s)"
        return Identification(OVERFLOW_TRAY, None, UNIDENTIFIED, attempt, details)

    def _light(self, level: int) -> None:
        if self.set_light is None:
            return
        try:
            self.set_light(level)
        except Exception as exc:                # never stop a run because of the light
            log.warning("could not set the chamber light to %d: %s", level, exc)


def _details(result) -> dict:
    d: dict = {
        "match_method": result.method.value if result.method else None,
        "confidence": result.confidence,
    }
    card = result.card
    if card is not None and result.identified:
        d.update(card_name=card.name, set_code=card.set_code, collector_number=card.collector_number,
                 color_identity=card.color_identity_str, type_line=card.type_line, mana_cost=card.mana_cost,
                 rarity=card.rarity)
    return d
