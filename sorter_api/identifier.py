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


@dataclass
class CaptureTrace:
    """One capture of a card (RF29), for the run benchmark and the cards_to_improve dataset
    (slow_cards.py). The pictures are references, not copies: they are dropped as soon as the
    controller knows the card was not slow, or once cards_to_improve has written them."""
    attempt: int
    light: int                          # chamber light level for this capture (RNF09)
    started_s: float                    # since the identification began
    wait_ms: int                        # light change / stop check before the photo (retries)
    capture_ms: int | None              # the photo (camera)
    recognition_ms: int | None          # crop + OCR + match (IdentificationResult.elapsed_ms)
    total_ms: int                       # this capture, end to end
    status: str                         # IDENTIFIED | LOW_CONFIDENCE | CROP_FAILED | FACE_DOWN | ERROR
    ocr_calls: int | None = None
    fallbacks: tuple[str, ...] = ()     # failsafes this capture needed (capture_fallbacks)
    error: str = ""
    result: object = field(default=None, repr=False)   # cardforge.cv IdentificationResult
    frame: object = field(default=None, repr=False)    # the camera picture (numpy BGR), if any
    summary: dict = field(default_factory=dict, repr=False)   # result.to_dict(), kept after drop_images()

    @property
    def rectified(self):
        return getattr(self.result, "rectified", None)

    def drop_images(self) -> None:
        self.frame = None
        if self.result is not None:
            if hasattr(self.result, "rectified"):
                self.result.rectified = None
            if hasattr(self.result, "frame"):
                self.result.frame = None

    def to_dict(self) -> dict:
        return {"attempt": self.attempt, "light": self.light, "started_s": self.started_s,
                "wait_ms": self.wait_ms, "capture_ms": self.capture_ms, "recognition_ms": self.recognition_ms,
                "total_ms": self.total_ms, "status": self.status, "ocr_calls": self.ocr_calls,
                "fallbacks": list(self.fallbacks), "error": self.error or None, "result": self.summary or None}


def capture_fallbacks(debug: dict) -> tuple[str, ...]:
    """Failsafes one recognition needed, from its debug (cardforge.cv recognizer): the shortest path
    is one OCR call (title + type + info at 0 degrees, layout OCR skipped), or none for a face-down
    card told by its color alone."""
    reasons = []
    reread = "score_rotated" in debug                    # the title was also read upside down
    if reread:
        reasons.append("orientation_reread")
    orientation_calls = 0 if debug.get("face_down_check") == "brown_only" else (2 if reread else 1)
    if (debug.get("ocr_calls") or 0) > orientation_calls:
        reasons.append("layout_ocr")
    if debug.get("face_down_check") == "after_identify":
        reasons.append("face_down_after_identify")
    return tuple(reasons)


@dataclass(frozen=True)
class Identification:
    tray: int
    card_identifier: str | None = None   # Scryfall print id
    scan_status: str = UNIDENTIFIED      # identified | unidentified | face_down
    attempts: int = 0
    details: dict = field(default_factory=dict)   # name, set, number, colors, method, confidence...
    notify: bool = True                  # False = no attention event (placeholder: nothing was scanned)
    fallbacks: tuple[str, ...] = ()      # why the card was not read at the first try (benchmark.py)
    trace: tuple = field(default=(), compare=False, repr=False)   # CaptureTrace of each capture


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
        traces: list[CaptureTrace] = []
        t0 = time.perf_counter()
        try:
            for attempt in range(1, self.attempts + 1):
                began = time.perf_counter()
                if attempt > 1:
                    sleep(0)                    # a stop request ends the retries here
                    level = min(100, level + self.light_step)
                    self._light(level)
                called = time.perf_counter()
                wait_ms = int((called - began) * 1000)
                try:
                    current = self.vision.identify()
                except Exception as exc:        # camera/vision fault: count it as a failed capture
                    log.exception("vision identify() failed")
                    last_error = str(exc)
                    traces.append(CaptureTrace(attempt, level, round(began - t0, 3), wait_ms, None, None,
                                               int((time.perf_counter() - began) * 1000), "ERROR",
                                               fallbacks=("vision_error",), error=str(exc)))
                    continue
                result = current
                debug = getattr(result, "debug", None) or {}
                # RF02 / RF15: the capture routine = (light change for a retry) + the photo.
                capture_ms = debug.get("capture_ms")
                if not isinstance(capture_ms, int):
                    capture_ms = None
                if capture_ms is not None:
                    slowest_capture_ms = max(slowest_capture_ms or 0, wait_ms + capture_ms)
                traces.append(CaptureTrace(
                    attempt, level, round(began - t0, 3), wait_ms, capture_ms, getattr(result, "elapsed_ms", None),
                    int((time.perf_counter() - began) * 1000), result.status.value, debug.get("ocr_calls"),
                    capture_fallbacks(debug), result=result, frame=getattr(result, "frame", None),
                    summary=_summary(result)))
                log.info("capture %d: %s %s%% %s (photo %s ms, recognition %s ms)", attempt, result.status.value,
                         result.confidence, result.card.name if result.card else "-", capture_ms,
                         getattr(result, "elapsed_ms", None))
                if result.status.is_final:
                    break
        finally:
            if level != self.light_base:
                self._light(self.light_base)

        trace = tuple(traces)
        if result is None:
            return Identification(OVERFLOW_TRAY, None, UNIDENTIFIED, attempt,
                                  {"error_message": f"vision error: {last_error}"},
                                  fallbacks=card_fallbacks(trace, UNIDENTIFIED), trace=trace)
        details = _details(result)
        if slowest_capture_ms is not None:
            details["capture_ms"] = slowest_capture_ms
        if result.status is IdentificationStatus.IDENTIFIED and result.card is not None:
            tray = tray_for_color_identity(result.card.color_identity)
            return Identification(tray, result.card.print_id, IDENTIFIED, attempt, details,
                                  fallbacks=card_fallbacks(trace, IDENTIFIED), trace=trace)
        if result.status is IdentificationStatus.FACE_DOWN:
            return Identification(OVERFLOW_TRAY, None, FACE_DOWN, attempt, details,
                                  fallbacks=card_fallbacks(trace, FACE_DOWN), trace=trace)
        details["error_message"] = f"{result.status.value} after {attempt} capture(s)"
        return Identification(OVERFLOW_TRAY, None, UNIDENTIFIED, attempt, details,
                              fallbacks=card_fallbacks(trace, UNIDENTIFIED), trace=trace)

    def _light(self, level: int) -> None:
        if self.set_light is None:
            return
        try:
            self.set_light(level)
        except Exception as exc:                # never stop a run because of the light
            log.warning("could not set the chamber light to %d: %s", level, exc)


def card_fallbacks(trace: tuple, scan_status: str) -> tuple[str, ...]:
    """Why a card was not read at the first try: empty = first-try (benchmark.py)."""
    reasons = ["retry"] if len(trace) > 1 else []
    for t in trace:
        reasons += [r for r in t.fallbacks if r not in reasons]
    if scan_status == UNIDENTIFIED and "not_identified" not in reasons:
        reasons.append("not_identified")
    return tuple(reasons)


def _summary(result) -> dict:
    try:
        return result.to_dict()
    except Exception:                           # test doubles without to_dict()
        return {"status": getattr(getattr(result, "status", None), "value", None),
                "confidence": getattr(result, "confidence", None)}


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
